from argparse import ArgumentParser, Namespace
import argparse
import os
import subprocess
from sys import stdout
from typing import Dict, List, Set, Union
from fhir.resources.codesystem import CodeSystem
from fhir.resources.fhirtypes import Boolean
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
from rich.logging import RichHandler
import logging


def configure_logging(level: str = "NOTSET", filename=None):
    handlers = [RichHandler(rich_tracebacks=True)]
    if filename != None:
        handlers.append(logging.FileHandler(
            os.path.abspath(filename), mode="w"))
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True
    )
    return logging.getLogger()


log = configure_logging()


def dir_path(string):
    "https://stackoverflow.com/a/51212150"
    if os.path.isdir(string):
        return string
    else:
        raise NotADirectoryError(string)


def parse_args():
    parser = ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--endpoint", type=str,
                        default="http://localhost:8080/fhir",
                        help="The FHIR TS endpoint")
    parser.add_argument("--authentication-credential", type=str,
                        help="An authentication credential. If blank, no authentication will be presented to the TS.")
    parser.add_argument("--authentication-type",
                        choices=["Bearer", "Basic"], default="Bearer",
                        help="The type of authentication credential")
    parser.add_argument("--input-directory",
                        type=dir_path,
                        help="Directory where resources should be converted from. Resources that are not FHIR Terminology resources in JSON are skipped (XML is NOT supported)!"
                        )
    parser.add_argument("--patch-dir", type=dir_path,
                        help="a directory where patches and modified files are written to. Not required, but recommended!")
    parser.add_argument("--log-level", type=str, choices=["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR"], default="INFO",
                        help="Log level")
    parser.add_argument("--log-file", type=str,
                        help="Filename where a log file should be written to. If not provided, output will only be provided to STDOUT")
    parser.add_argument("files", nargs="*", type=argparse.FileType("r"),
                        help="You can list JSON files that should be converted, independent of the input dir parameter. XML is NOT supported")
    args = parser.parse_args()
    log = configure_logging(args.log_level, args.log_file)
    if args.patch_dir == None:
        log.warning(
            "No patch directory is specified and patches will NOT be written. This is not recommended!")
    if (args.files == [] and args.input_directory == None):
        parser.print_help()
        exit(1)
    editor = os.getenv("EDITOR")
    if editor == None:
        log.warning(
            "No editor is configured using the variable $EDITOR ! This may lead to undefined behaviour when opening files!")
    else:
        log.info(f"Using editor: '{editor}'")
    log.info("Command line arguments:")
    for arg in vars(args):
        log.info(f" - {arg} : {getattr(args, arg)}")
    input("Press any key to continue.")
    return args


def gather_files(args: Namespace):
    files: Dict[str, str] = {tw.name: "".join(
        tw.readlines()) for tw in args.files}
    if args.input_directory != None:
        log.info(f"using resources from {args.input_directory}")
        for f in os.listdir(args.input_directory):
            try:
                with open(os.path.join(args.input_directory, f), "r", encoding="utf-8") as fp:
                    files[fp.name] = "".join(fp.readlines())
            except:
                log.info(
                    f"file {f} in {args.input_directory} could not be parsed as a UTF-8 Text file. It will be ignored.")
    if len(files) == 0:
        log.info("There are no files provided!")
        exit(1)
    else:
        return files


def validate_files(args: Namespace, files):
    valid_resources = {}
    for filename, file_content in files.items():
        issues = []
        log.info(filename)
        try:
            parsed_json = json.loads(file_content)
            fhir_resource = None
            if ("resourceType" in parsed_json):
                resourceType = parsed_json["resourceType"]
                if (resourceType == "CodeSystem"):
                    fhir_resource = CodeSystem.parse_obj(parsed_json)
                    log.info(f"CodeSystem {fhir_resource.name} ")
                elif (resourceType == "ValueSet"):
                    fhir_resource = ValueSet.parse_obj(parsed_json)
                    log.info(f"ValueSet {fhir_resource.name} ")
                elif (resourceType == "ConceptMap"):
                    fhir_resource = ConceptMap.parse_obj(parsed_json)
                    log.info(f"ConceptMap {fhir_resource.name} ")
                elif (resourceType == "NamingSystem"):
                    fhir_resource = NamingSystem.parse_obj(parsed_json)
                    log.info(f"NamingSystem {fhir_resource.name} ")
                else:
                    issues.append(
                        f"The resource type {resourceType} is not supported by this script!")
                if (fhir_resource != None):
                    valid_resources[filename] = fhir_resource

        except:
            issues.append(
                "The resource could not be parsed as FHIR. If it is in XML format, please convert it to JSON!")

        if len(issues) > 0:
            log.warning(
                "The file can not be converted due to the following issue(s):")
            log.warning(issues)
        log.info("\n")
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
    log.info("\n" * 2)
    log.info("##########")
    log.info(f"Uploading resources to {base.geturl()}...")
    session = requests.session()
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })
    session.proxies = getproxies()
    if (getproxies()):
        log.info("Using proxy: ", getproxies(), sep=" ")
    if (args.authentication_credential != None):
        auth = f"{args.authentication_type} {args.authentication_credential}"
        log.info(f"Using auth header: '{auth[:10]}...'")
        session.headers.update(
            {"Authorization": auth})

    for resource_list in sorted_resources:
        for filename, loaded_resource in resource_list.items():
            # log.info("\n")
            res = loaded_resource
            resource_type = res.resource_type
            log.info(
                f"{resource_type} {res.name}, version {res.version} @ {filename}")
            method = "PUT"
            if (res.id == None):
                log.warning(
                    "The resource has no ID specified. That is not optimal! If you want to specify an ID, do so now. " +
                    "If you provide nothing, the ID will be autogenerated by the server.")
                new_id = input("ID? ").strip()
                if (new_id == ""):
                    log.info("Using autogenerated ID and POST")
                    endpoint: str = urljoin(base.geturl(), resource_type)
                    method = "POST"
                else:
                    log.info(f"Using provided ID {new_id}")
                    res.id = new_id
            if method == "PUT":
                endpoint: str = urljoin(
                    base.geturl(), f"{resource_type}/{res.id}")
            log.info(f"Using {method} to {endpoint}")

            upload_success = False
            count_uploads = 0
            while (not upload_success and count_uploads <= max_tries):
                count_uploads += 1
                log.info(
                    f"uploading (try #{count_uploads}/{max_tries})")
                js = json.loads(res.json())
                prepared_rx = Request(method=method, url=endpoint,
                                      headers=session.headers,
                                      json=js).prepare()
                request_result = session.send(prepared_rx)
                log.info(
                    f"received status code {request_result.status_code}")
                if request_result.status_code >= 200 and request_result.status_code < 300:
                    created_id = request_result.json()["id"]
                    log.info(
                        f"The resource was created successfully at {created_id}")
                    resource_url = request_result.headers.get(
                        'Content-Location', f"{endpoint}/{created_id}")
                    log.info(
                        f"URL of the resource: {resource_url}")
                    if (res.resource_type == "ValueSet"):
                        log.info(
                            "The resource is a ValueSet. Attempting expansion!")
                        upload_success = try_expand_valueset(
                            session, endpoint, res)
                        if upload_success:
                            log.info(
                                f"The ValueSet was expanded successfully at {created_id}")
                    else:
                        upload_success = True
                else:
                    log.error("This status code means an error occurred.")
                    print_operation_outcome(request_result)
                if not upload_success:
                    choices = [
                        inquirer.List('action',
                                      "What should we do?",
                                      choices=[("Edit (using your editor from $EDITOR)", "Edit"),
                                               ("Ignore (continue with the next resource)", "Ignore"),
                                               ("Retry (because you have changed/uploaded something else)", "Retry")
                                               ])
                    ]
                    stdout.flush()
                    action = inquirer.prompt(choices)['action']
                    stdout.flush()
                    if action == "Ignore":
                        log.warning(
                            "The file is ignored. Proceeding with the next file.")
                        break
                    elif action == "Retry":
                        log.warning("Trying to upload file again.")
                    else:
                        edited_file = None
                        while (edited_file == None):
                            edited_file = edit_file(
                                filename, res, count_uploads, args.patch_dir)
                        res = edited_file
                    continue
                else:
                    log.info("The resource %s was successfully uploaded (try: %d)\n\n",
                             f"{res.resource_type} {res.name}, version {res.version} @ {filename}", count_uploads)
                    choices = [
                        inquirer.List("action",
                                      "Do you want to edit the uploaded resource manually?",
                                      choices=[
                                          ("No (continue with the next resource)", "no"),
                                          ("Yes (open the file using $EDITOR)", "yes")])]
                    stdout.flush()
                    action = inquirer.prompt(choices)['action'].strip().lower()
                    stdout.flush()
                    if action == "yes":
                        edited_file = None
                        while (edited_file == None):
                            edited_file = edit_file(
                                filename, res, count_uploads, args.patch_dir, manual=True)
                        res = edited_file
                        upload_success = False
                    else:
                        log.info("The file was accepted. Continuing.")


def print_operation_outcome(result: Response):
    try:
        op_outcome = OperationOutcome.parse_obj(
            result.json())
        issue = [i.json() for i in op_outcome.issue]
        log.error("FHIR OperationOutcome Issue: %s",
                  issue)
    except Exception:
        log.error(
            "Could not parse the result as JSON/OperationOutcome! Here is the raw response: %s: ", result.text)
        log.exception("This exception was thrown.")


def edit_file(filename: str, resource: Union[NamingSystem, CodeSystem, ValueSet, ConceptMap], count_uploads: int, patch_dir: str, manual: Boolean = False):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=os.path.basename(filename), suffix=f"{count_uploads}.json") as temp_fp:
        temp_filename = temp_fp.name
        js = json.loads(resource.json())
        original_text = json.dumps(js, indent=2)
        json.dump(js, temp_fp, indent=2)
        temp_fp.flush()
        try:
            edited_file = editor.edit(filename=temp_filename)
        except Exception as e:
            log.exception(
                f"An error occurred when editing {temp_filename}", e)
            return None
        try:
            js = json.loads(edited_file)
        except Exception as e:
            log.exception(
                "An error occurred when parsing the edited file as JSON", e)
            return None
        try:
            edited_text = json.dumps(js, indent=2)
            raw_filename = f"{os.path.basename(filename)}-revision{count_uploads}"
            if (manual):
                raw_filename += "_manual"
            if patch_dir != None:
                patch_filename = os.path.join(
                    patch_dir, f"{raw_filename}.patch")

                #dmp = dmp_module.diff_match_patch()
                #patch = dmp.patch_make(original_text, edited_text)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=raw_filename, suffix="-original.json") as original_tempfp:
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=raw_filename, suffix="-patch.json") as edited_tempfp:
                        original_tempfp.write(original_text)
                        original_tempfp.flush()
                        edited_tempfp.write(edited_text)
                        edited_tempfp.flush()
                        with open(patch_filename, "wb") as patch_fp:
                            command = f"diff {os.path.abspath(original_tempfp.name)} {os.path.abspath(edited_tempfp.name)}"
                            process = subprocess.Popen(
                                command.split(), stdout=subprocess.PIPE)
                            patch, error = process.communicate()
                            if (error == None):
                                patch_fp.write(patch)
                            else:
                                log.error(
                                    f"Error writing diff to {patch_filename}")
                log.info(
                    f"Wrote patch file for revision {count_uploads} to {patch_filename}")
                edited_filename = os.path.join(
                    patch_dir, f"{raw_filename}.edited")
                with open(edited_filename, "w") as edited_fp:
                    edited_fp.write(edited_text)
                log.info(
                    f"Wrote edited file for revision {count_uploads} to {edited_filename}")
        except Exception:
            log.exception("An error occurred writing the patch.")
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
            log.exception(
                f"The edited file could not be parsed as a FHIR {resource.resource_type}!", e)


def try_expand_valueset(session: Session, endpoint: str, vs: ValueSet) -> bool:
    expansion_endpoint = f"{endpoint}/$expand"
    expand_request = Request(
        method="GET", url=expansion_endpoint, headers=session.headers).prepare()
    expansion_result = session.send(expand_request)
    status = expansion_result.status_code
    if status >= 200 and status < 300:

        log.info(
            f"Expansion operation completed successfully with status code {status}")
        try:
            expansion_vs = ValueSet.parse_obj(expansion_result.json())
            expansion: ValueSetExpansion = expansion_vs.expansion
            if (expansion.contains == None):
                log.error("There is no expansion.contains in the expansion, " +
                          "meaning that there are no concepts in the ValueSet. This is an error!")
                return False
            number_concepts = len(expansion.contains)
            log.info(
                f"Expanded ValueSet contains {number_concepts} concepts")
            contained_codesystems: Set[str] = set(
                [i.system for i in vs.compose.include])
            system_map = list(map(lambda x: x.system, expansion.contains))
            system_counts = {l: system_map.count(l) for l in set(system_map)}
            # if len(system_counts.keys()) > 1:
            log.info("Concepts by system: %s", system_counts)
            empty_systems = [x for x, c in system_counts.items() if c ==
                             0 and x in contained_codesystems]
            missing_expand_systems = [
                x for x in contained_codesystems if x not in system_counts.keys()]
            if any(empty_systems):
                log.error("The following systems have no concepts: ",
                          empty_systems)
                log.error("This should be regarded as an error!")
                return False
            elif any(missing_expand_systems):
                log.error(
                    "There are code systems referenced in the `compose.include` of the ValueSet, but missing in the expansion:", missing_expand_systems)
                return False
            return True
        except Exception as e:
            log.error("The reply could not be parsed as a ValueSet! Here is the raw response: %s",
                      expansion_result.text)
            log.exception("The error is:")
            return False
    else:
        log.error("This was an error in expansion!")
        print_operation_outcome(expansion_result)
        return False


if __name__ == "__main__":
    args = parse_args()
    files = gather_files(args)
    valid_resources = validate_files(args, files)
    sorted_resources = sort_resources(valid_resources)
    upload_resources(args, sorted_resources)
    log.info("We are done!")
