from argparse import ArgumentParser, Namespace
import argparse
import os
from os import path
from typing import Dict, List, Union
from fhir.resources.codesystem import CodeSystem
from fhir.resources.valueset import ValueSet
from fhir.resources.conceptmap import ConceptMap
from fhir.resources.namingsystem import NamingSystem
from fhir.resources.operationoutcome import OperationOutcome
from fhir.resources.codeableconcept import CodeableConcept
import json
from urllib.parse import urlparse, urljoin
from urllib.request import getproxies
from inquirer.shortcuts import editor
import requests
from requests.sessions import Request
import inquirer
import tempfile
import editor
import diff_match_patch as dmp_module


def dir_path(string):
    "https://stackoverflow.com/a/51212150"
    if os.path.isdir(string):
        return string
    else:
        raise NotADirectoryError(string)


def parse_args():
    parser = ArgumentParser()
    parser.add_argument("--endpoint", type=str,
                        default="http://localhost:8080/fhir")
    parser.add_argument("--authentication-credential", type=str)
    parser.add_argument("--authentication-type",
                        choices=["Bearer", "Basic"], default="Bearer")
    parser.add_argument("--input-directory", type=dir_path)
    parser.add_argument("--patch-dir", type=dir_path)
    parser.add_argument("files", nargs="*", type=argparse.FileType("r"))
    return parser


def validate_files(args: Namespace):
    valid_resources = {}
    files: Dict[str, str] = {tw.name: "".join(
        tw.readlines()) for tw in args.files}
    if args.input_directory != None:
        print(f"using resources from {args.input_directory}")
        for f in os.listdir(args.input_directory):
            try:
                with open(os.path.join(args.input_directory, f), "r", encoding="utf-8") as fp:
                    files[fp.name] = "".join(fp.readlines())
            except:
                print(
                    f"file {f} in {args.input_directory} could not be parsed as a UTF-8 Text file. It will be ignored.")
    for filename, file_content in files.items():
        errors = []
        print(filename)
        try:
            parsed_json = json.loads(file_content)
            fhir_resource = None
            if ("resourceType" in parsed_json):
                resourceType = parsed_json["resourceType"]
                if (resourceType == "CodeSystem"):
                    fhir_resource = CodeSystem.parse_obj(parsed_json)
                    print(f" - CodeSystem {fhir_resource.name} ")
                elif (resourceType == "ValueSet"):
                    fhir_resource = ValueSet.parse_obj(parsed_json)
                    print(f" - ValueSet {fhir_resource.name} ")
                elif (resourceType == "ConceptMap"):
                    fhir_resource = ConceptMap.parse_obj(parsed_json)
                    print(f" - ConceptMap {fhir_resource.name} ")
                elif (resourceType == "NamingSystem"):
                    fhir_resource = NamingSystem.parse_obj(parsed_json)
                    print(f" - NamingSystem {fhir_resource.name} ")
                else:
                    errors.append(
                        f"The resource type {resourceType} is not supported by this script!")
                if (fhir_resource != None):
                    valid_resources[filename] = fhir_resource

        except:
            errors.append(
                "The resource could not be parsed as FHIR. If it is in XML format, please convert it to JSON!")

        if len(errors) > 0:
            print(
                "! The file can not be converted due to the following error(s):", sep="\n - ")
            print("\n - ".join(errors))
        print()
    return valid_resources


def sort_resources(resources: Dict[str, Union[NamingSystem, CodeSystem, ValueSet, ConceptMap]]):
    codesystems = {fn: f for fn,
                   f in resources.items() if isinstance(f, CodeSystem)}
    valuesets = {fn: f for fn, f in resources.items()
                 if isinstance(f, ValueSet)}
    conceptmaps = {fn: f for fn,
                   f in resources.items() if isinstance(f, ConceptMap)}
    namingsystems = {fn: f for fn,
                     f in resources.items() if isinstance(f, NamingSystem)}

    return list([namingsystems, codesystems, valuesets, conceptmaps])


def upload_resources(args: Namespace, sorted_resources: List[Dict[str, Union[NamingSystem, CodeSystem, ValueSet, ConceptMap]]], max_tries: int = 10):
    base = urlparse(args.endpoint.rstrip('/') + "/")
    print(f"\n\n")
    print("##########")
    print(f"Uploading resources to {base.geturl()}...")
    session = requests.session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })
    session.proxies = getproxies()
    if (args.authentication_credential != None):
        auth = f"{args.authentication_type} {args.authentication_credential}"
        print(f"Using auth header: '{auth[:10]}...'")
        session.headers.update(
            {"Authorization": auth})
    for resource_list in sorted_resources:
        for filename, res in resource_list.items():
            current_resource = res
            resource_type = res.resource_type
            print(f"{resource_type} {res.name}, version {res.version}")
            method = "PUT"
            if (res.id == None):
                print(" - The resource has no ID specified. That is not optimal! If you want to specify an ID, do so now. If you provide nothing, the ID will be autogenerated by the server.")
                new_id = input("ID? ").strip()
                if (new_id == ""):
                    endpoint: str = urljoin(base.geturl(), resource_type)
                    method = "POST"
                else:
                    res.id = new_id
            if method == "PUT":
                endpoint: str = urljoin(
                    base.geturl(), f"{resource_type}/{res.id}")
            print(f" -> {method} {endpoint}")

            upload_success = False
            count_uploads = 1
            while (not upload_success and count_uploads <= max_tries):
                print(f" - uploading (try #{count_uploads}/{max_tries})")
                js = json.loads(current_resource.json())
                prepared_rx = Request(method=method, url=endpoint,
                                      json=js).prepare()
                request_result = session.send(prepared_rx)
                print(f" - received status code {request_result.status_code}")
                if request_result.status_code >= 200 and request_result.status_code < 300:
                    created_id = request_result.json()["id"]
                    print(
                        f" + The resource was created successfully at {created_id}")
                    if (res.resource_type == "ValueSet"):
                        print("the resource is a ValueSet. Attempting expansion!")
                        upload_success = try_expand_valueset(endpoint, res)
                    else:
                        upload_success = True
                else:
                    print(" ! This status code means an error occurred.")
                    try:
                        op_outcome = OperationOutcome.parse_obj(
                            request_result.json())
                        issues = []
                        for iss in op_outcome.issue:
                            if iss.details != None:
                                cc: CodeableConcept = iss.details
                                issues.append(cc.json())
                            join_issues = " -!".join(issues)
                        print(f" -! {join_issues}")
                    except:
                        print(
                            "Could not parse the result as JSON! Here is the raw response.")
                        print(request_result.text())

                    choices = [
                        inquirer.List('action',
                                      "What should we do?",
                                      choices=[("Ignore (continue with the next resource)", "Ignore"),
                                               ("Edit (using your editor from $EDITOR)", "Edit"),
                                               ("Retry (because you have changed something else)", "Retry")
                                               ])
                    ]
                    action = inquirer.prompt(choices)['action']
                    if action == "Ignore":
                        print("The file is ignored. Proceeding with the next file.")
                        break
                    elif action == "Retry":
                        print("We will try again!")
                    else:
                        edited_file = None
                        while (edited_file == None):
                            edited_file = edit_file(
                                filename, current_resource, count_uploads, args.patch_dir)
                        current_resource = edited_file
                    count_uploads += 1
                    continue


def edit_file(filename: str, resource: Union[NamingSystem, CodeSystem, ValueSet, ConceptMap], count_uploads: int, patch_dir: str):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=os.path.basename(filename), suffix=f"{count_uploads}.json") as temp_fp:
        temp_filename = temp_fp.name
        js = json.loads(resource.json())
        original_text = json.dumps(js, indent=2)
        json.dump(js, temp_fp, indent=2)
        temp_fp.flush()
        try:
            edited_file = editor.edit(filename=temp_filename)
        except Exception as e:
            print(f"An error occurred when editing {temp_filename}")
            return None
        try:
            js = json.loads(edited_file)
        except Exception as e:
            print("An error occurred when parsing the edited file as JSON", e)
            return None
        try:
            edited_text = json.dumps(js, indent=2)
            if patch_dir != None:
                patch_filename = os.path.join(
                    patch_dir, f"{os.path.basename(filename)}-revision{count_uploads}.patch")
                dmp = dmp_module.diff_match_patch()
                patch = dmp.patch_make(original_text, edited_text)
                with open(patch_filename, "w") as patch_fp:
                    patch_fp.write(dmp.patch_toText(patch))
                print(
                    f"Wrote patch file for revision {count_uploads} to {patch_filename}")
                edited_filename = os.path.join(
                    patch_dir, f"{os.path.basename(filename)}-revision{count_uploads}.edited")
                with open(edited_filename, "w") as edited_fp:
                    edited_fp.write(edited_text)
                print(
                    f"Wrote edited file for revision {count_uploads} to {edited_filename}")
        except:
            print("An error occurred writing the patch.")
        try:
            if resource.resource_type == "NamingSystem":
                return NamingSystem.parse_file(temp_filename)
            elif resource.resource_type == "CodeSystem":
                return CodeSystem.parse_file(temp_filename)
            elif resource.resource_type == "ValueSet":
                return ValueSet.parse_file(temp_filename)
            elif resource.resource_type == "ConceptMap":
                return ConceptMap.parse_file(temp_filename)
        except Exception as e:
            print(
                f"The edited file could not be parsed as a FHIR {resource.resource_type}!", e)


def try_expand_valueset():
    # TODO
    return False


if __name__ == "__main__":
    parser = parse_args()
    args = parser.parse_args()
    valid_resources = validate_files(args)
    sorted_resources = sort_resources(valid_resources)
    upload_resources(args, sorted_resources)
