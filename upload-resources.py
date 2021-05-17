from argparse import ArgumentParser, Namespace
import argparse
import os
from typing import Dict, List, Set, Union
from urllib import request
from fhir.resources.codesystem import CodeSystem
from fhir.resources.valueset import ValueSet, ValueSetExpansion
from fhir.resources.conceptmap import ConceptMap
from fhir.resources.namingsystem import NamingSystem
from fhir.resources.operationoutcome import OperationOutcome
import json
from urllib.parse import urlparse, urljoin
from urllib.request import getproxies
from inquirer.shortcuts import editor
import requests
from requests.models import Response
from requests.sessions import Request, Session
import inquirer
import tempfile
import editor
import diff_match_patch as dmp_module
from rich.console import Console
from rich.theme import Theme
from collections import Counter


custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold magenta",
    "low-warning": "magenta",
    "error": "bold red",
    "success": "green"
})
console = Console(theme=custom_theme)


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
        console.log(f"using resources from {args.input_directory}")
        for f in os.listdir(args.input_directory):
            try:
                with open(os.path.join(args.input_directory, f), "r", encoding="utf-8") as fp:
                    files[fp.name] = "".join(fp.readlines())
            except:
                console.log(
                    f"file {f} in {args.input_directory} could not be parsed as a UTF-8 Text file. It will be ignored.", style="low-warning")
    for filename, file_content in files.items():
        issues = []
        console.log(filename)
        try:
            parsed_json = json.loads(file_content)
            fhir_resource = None
            if ("resourceType" in parsed_json):
                resourceType = parsed_json["resourceType"]
                if (resourceType == "CodeSystem"):
                    fhir_resource = CodeSystem.parse_obj(parsed_json)
                    console.log(f"CodeSystem {fhir_resource.name} ")
                elif (resourceType == "ValueSet"):
                    fhir_resource = ValueSet.parse_obj(parsed_json)
                    console.log(f"ValueSet {fhir_resource.name} ")
                elif (resourceType == "ConceptMap"):
                    fhir_resource = ConceptMap.parse_obj(parsed_json)
                    console.log(f"ConceptMap {fhir_resource.name} ")
                elif (resourceType == "NamingSystem"):
                    fhir_resource = NamingSystem.parse_obj(parsed_json)
                    console.log(f"NamingSystem {fhir_resource.name} ")
                else:
                    issues.append(
                        f"The resource type {resourceType} is not supported by this script!")
                if (fhir_resource != None):
                    valid_resources[filename] = fhir_resource

        except:
            issues.append(
                "The resource could not be parsed as FHIR. If it is in XML format, please convert it to JSON!")

        if len(issues) > 0:
            console.log(
                "The file can not be converted due to the following issue(s):", style="low-warning")
            console.log(issues, style="low-warning")
        console.line()
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
    console.line(2)
    console.log("##########")
    console.log(f"Uploading resources to {base.geturl()}...")
    session = requests.session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })
    session.proxies = getproxies()
    if (getproxies()):
        console.log("Using proxy: ", getproxies(), style="info", sep=" ")
    if (args.authentication_credential != None):
        auth = f"{args.authentication_type} {args.authentication_credential}"
        console.log(f"Using auth header: '{auth[:10]}...'", style="info")
        session.headers.update(
            {"Authorization": auth})

    for resource_list in sorted_resources:
        for filename, res in resource_list.items():
            console.line()
            current_resource = res
            resource_type = res.resource_type
            console.log(f"{resource_type} {res.name}, version {res.version}")
            method = "PUT"
            if (res.id == None):
                console.log(
                    "The resource has no ID specified. That is not optimal! If you want to specify an ID, do so now. " +
                    "If you provide nothing, the ID will be autogenerated by the server.", style="warning")
                new_id = input("ID? ").strip()
                if (new_id == ""):
                    endpoint: str = urljoin(base.geturl(), resource_type)
                    method = "POST"
                else:
                    res.id = new_id
            if method == "PUT":
                endpoint: str = urljoin(
                    base.geturl(), f"{resource_type}/{res.id}")
            console.log(f"Using {method} to {endpoint}", style="info")

            upload_success = False
            count_uploads = 0
            while (not upload_success and count_uploads <= max_tries):
                count_uploads += 1
                console.log(
                    f"uploading (try #{count_uploads}/{max_tries})", style="info")
                js = json.loads(current_resource.json())
                prepared_rx = Request(method=method, url=endpoint,
                                      headers=session.headers,
                                      json=js).prepare()
                request_result = session.send(prepared_rx)
                console.log(
                    f"received status code {request_result.status_code}")
                if request_result.status_code >= 200 and request_result.status_code < 300:
                    created_id = request_result.json()["id"]
                    console.log(
                        f"The resource was created successfully at {created_id}", style="success")
                    resource_url = request_result.headers.get(
                        'Content-Location', f"{endpoint}/{created_id}")
                    console.log(
                        f"URL of the resource: {resource_url}", style="success")
                    if (res.resource_type == "ValueSet"):
                        console.log(
                            "The resource is a ValueSet. Attempting expansion!", style="low-warning")
                        upload_success = try_expand_valueset(
                            session, endpoint, res)
                        if upload_success:
                            console.log(
                                f"The ValueSet was expanded successfully at {created_id}", style="success")
                    else:
                        upload_success = True
                else:
                    console.log(
                        "This status code means an error occurred.", style="error")
                    print_operation_outcome(request_result)
                if not upload_success:
                    choices = [
                        inquirer.List('action',
                                      "What should we do?",
                                      choices=[("Ignore (continue with the next resource)", "Ignore"),
                                               ("Edit (using your editor from $EDITOR)", "Edit"),
                                               ("Retry (because you have changed/uploaded something else)", "Retry")
                                               ])
                    ]
                    action = inquirer.prompt(choices)['action']
                    if action == "Ignore":
                        console.log(
                            "The file is ignored. Proceeding with the next file.", style="warning")
                        break
                    elif action == "Retry":
                        console.log("We will try again!", style="info")
                    else:
                        edited_file = None
                        while (edited_file == None):
                            edited_file = edit_file(
                                filename, current_resource, count_uploads, args.patch_dir)
                        current_resource = edited_file
                    continue


def print_operation_outcome(result: Response):
    try:
        op_outcome = OperationOutcome.parse_obj(
            result.json())
        issue = [i.json() for i in op_outcome.issue]
        console.log("FHIR OperationOutcome Issue: ",
                    issue, style="error")
    except:
        console.log(
            "Could not parse the result as JSON/OperationOutcome! Here is the raw response.", style="error")
        console.log(result.text, style="error")


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
            console.log(
                f"An error occurred when editing {temp_filename}", e, style="error")
            return None
        try:
            js = json.loads(edited_file)
        except Exception as e:
            console.log(
                "An error occurred when parsing the edited file as JSON", e, style="error")
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
                console.log(
                    f"Wrote patch file for revision {count_uploads} to {patch_filename}")
                edited_filename = os.path.join(
                    patch_dir, f"{os.path.basename(filename)}-revision{count_uploads}.edited")
                with open(edited_filename, "w") as edited_fp:
                    edited_fp.write(edited_text)
                console.log(
                    f"Wrote edited file for revision {count_uploads} to {edited_filename}")
        except:
            console.log("An error occurred writing the patch.")
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
            console.log(
                f"The edited file could not be parsed as a FHIR {resource.resource_type}!", e)


def try_expand_valueset(session: Session, endpoint: str, vs: ValueSet) -> bool:
    expansion_endpoint = f"{endpoint}/$expand"
    expand_request = Request(
        method="GET", url=expansion_endpoint, headers=session.headers).prepare()
    expansion_result = session.send(expand_request)
    status = expansion_result.status_code
    if status >= 200 and status < 300:
        console.log(
            f"Expansion operation completed successfully with status code {status}")
        expansion_vs = ValueSet.parse_obj(expansion_result.json())
        expansion: ValueSetExpansion = expansion_vs.expansion
        number_concepts = len(expansion.contains)
        console.log(
            f"Expanded ValueSet contains {number_concepts} concepts")
        contained_codesystems: Set[str] = set(
            [i.system for i in vs.compose.include])
        # moved these assignments to the listcomp above!
        # for include_item in vs.compose.include:
        # contained_codesystems.add(
        #     f"{include_item.system}, version {include_item.version}")
        # contained_codesystems.add(include_item.system)
        # system_map = list(map(
        #     lambda x: f"{x.system}, version {x.version}", expansion.contains))
        system_map = list(map(lambda x: x.system, expansion.contains))
        system_counts = {l: system_map.count(l) for l in set(system_map)}
        # if len(system_counts.keys()) > 1:
        console.log("Concepts by system: ", system_counts, style="info")
        empty_systems = [x for x, c in system_counts.items() if c ==
                         0 and x in contained_codesystems]
        missing_expand_systems = [
            x for x in contained_codesystems if x not in system_counts.keys()]
        if any(empty_systems):
            console.log("The following systems have no concepts: ",
                        empty_systems, style="error")
            console.log("This should be regarded as an error!")
            return False
        elif any(missing_expand_systems):
            console.log(
                "There are code systems referenced in the `compose.include` of the ValueSet, but missing in the expansion:", missing_expand_systems, style="error")
            return False
        return True
    else:
        console.log("This was an error in expansion!", style="error")
        print_operation_outcome(expansion_result)
        return False


if __name__ == "__main__":
    parser = parse_args()
    args = parser.parse_args()
    valid_resources = validate_files(args)
    sorted_resources = sort_resources(valid_resources)
    upload_resources(args, sorted_resources)
