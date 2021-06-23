from argparse import ArgumentParser, Namespace
import argparse
import os
import subprocess
from sys import stdout
from typing import Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4
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
from requests.models import HTTPBasicAuth, Response
from requests.sessions import Request, Session
import inquirer
import tempfile
import editor
from rich.logging import RichHandler
import logging
from rauth import OAuth2Service
import pkce
from urllib.parse import urlparse, parse_qs
from datetime import datetime, time, timedelta


class EncapsulatedOAuth2Token:
    auth_token: str
    refresh_token: str
    token_url: str
    expires_seconds: int
    expires_at: datetime
    refresh_expires_seconds: int
    refresh_expires_at: datetime
    client_auth: HTTPBasicAuth
    cert: Optional[Tuple[str, str]]
    log: logging.Logger
    requested_at: datetime
    print_auth_token: bool = True

    def __init__(self, oauth_response, token_url, client_auth, cert, log, requested_at=datetime.now(), refresh_tolerance: float = 0.2) -> None:
        self.token_url = token_url
        self.client_auth = client_auth
        self.cert = cert
        self.log = log
        self.refresh_tolerance = refresh_tolerance
        self.parse_oauth_response(oauth_response, requested_at)

    def parse_oauth_response(self, oauth_response: Dict[str, str], requested_at: datetime) -> None:
        self.requested_at = requested_at
        if "error" in oauth_response:
            raise RuntimeError(
                f"Error requesting OAuth2 token with error '{oauth_response['error']}' ({oauth_response['error_description']}")
        else:
            self.auth_token = oauth_response["access_token"]
            self.refresh_token = oauth_response["refresh_token"]
            self.expires_seconds = oauth_response["expires_in"]
            self.refresh_expires_seconds = oauth_response["refresh_expires_in"]
            self.refresh_expires_at = self.requested_at + \
                timedelta(seconds=self.refresh_expires_seconds)
            self.expires_at = self.requested_at + \
                timedelta(seconds=self.expires_seconds)
            if (self.print_auth_token):
                print(f"Auth token: {self.auth_token}")

    def freshness(self, expires_at: datetime) -> float:
        delta: timedelta = expires_at - datetime.now()
        if (delta.seconds < 0):
            return 0.0
        return round(delta.seconds / float(self.refresh_expires_seconds), 2)

    def refresh_freshness(self) -> float:
        return self.freshness(self.refresh_expires_at)

    def token_freshness(self) -> float:
        return self.freshness(self.expires_at)

    def __repr__(self) -> str:
        return f"OAuth[Access={self.auth_token[:8]}...;" + \
            f"Refresh={self.refresh_token[:8]}...;" + \
            f"Expiry={self.expires_at}" + \
            f"(freshness={self.token_freshness()}, refresh freshness={self.refresh_freshness()}; tolerance={self.refresh_tolerance})]"

    def needs_refresh(self) -> bool:
        if datetime.now() > self.expires_at:
            self.log.debug("Access token is expired, refreshing")
            return True
        elif self.refresh_freshness() <= self.refresh_tolerance:
            self.log.debug(
                f"Refresh token is {self.refresh_freshness() * 100}% fresh and at risk of expiring, refreshing early")
            return True
        elif self.token_freshness() <= self.refresh_tolerance:
            self.log.debug(
                f"Access token is {self.token_freshness() * 100}% fresh and at risk of expiring, refreshing early")
        else:
            valid_remaining = self.expires_at - datetime.now()
            log.debug(
                f"Token valid for another {valid_remaining.seconds}s ({self.token_freshness() * 100}% fresh)")
            return False

    def can_refresh(self) -> bool:
        return datetime.now() < self.refresh_expires_at

    def refresh(self):
        auth_params = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
        headers = {
            "Accept": "application/json"
        }
        requested_at = datetime.now()
        oauth_response = requests.post(self.token_url,
                                       data=auth_params,
                                       headers=headers,
                                       auth=self.client_auth,
                                       cert=self.cert)
        self.parse_oauth_response(oauth_response.json(), requested_at)
        self.log.info(
            f"Refreshed OAuth2 token, valid for {self.expires_seconds}s")

    def apply_authorization(self, session: requests.Session) -> bool:
        if self.needs_refresh():
            if self.can_refresh():
                self.refresh()
            else:
                return False
        session.headers.update({"Authorization": f"Bearer {self.auth_token}"})
        return True


def configure_logging(level: str = "NOTSET", filename=None):
    handlers = [RichHandler(rich_tracebacks=True)]
    if filename != None:
        formatter = logging.Formatter(fmt="%(levelname)s %(message)s")
        filehandler = logging.FileHandler(
            os.path.abspath(filename), mode="w")
        filehandler.setFormatter(formatter)
        handlers.append(filehandler)
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
    required_group = parser.add_argument_group("Required")
    required_group.add_argument("--endpoint", type=str,
                                default="http://localhost:8080/fhir",
                                help="The FHIR TS endpoint",
                                required=True)
    auth_group = parser.add_argument_group("Authentication")

    auth_group.add_argument("--basic-authentication", type=str,
                            help="An Basic authentication credential")
    auth_group.add_argument("--bearer-authentication", type=str,
                            help="An Bearer authentication credential")
    auth_group.add_argument("--oauth-authorize",
                            help="OAuth2 Authorization URL", type=str)
    auth_group.add_argument("--oauth-token", help="OAuth Token URL", type=str)
    auth_group.add_argument("--oauth-client-id",
                            help="OAuth Client ID", type=str)
    auth_group.add_argument("--oauth-client-secret",
                            help="OAuth Client Secret", type=str)
    auth_group.add_argument(
        "--oauth-redirect", help="Redirect URL for OIDC. Must be legal in the authentication server configuration", type=str)
    auth_group.add_argument(
        "--oauth-pkce", help="If provided, use PKCE for authentication", action="store_true")
    auth_group.add_argument("--cert", type=str,
                            help="Provide a PKI keypair to use for mutual TLS authentication. You can either provide a single file path, containing both " +
                            "the public and private key (often .pem) or two files, seperated by '|': first public key (often .crt), then private key (often .key)")

    trace_group = parser.add_argument_group("Traceability")
    trace_group.add_argument("--patch-directory", type=dir_path,
                             help="a directory where patches and modified files are written to. Not required, but recommended!")

    trace_group.add_argument("--log-level", type=str, choices=["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR"], default="INFO",
                             help="Log level")
    trace_group.add_argument("--log-file", type=str,
                             help="Filename where a log file should be written to. If not provided, output will only be provided to STDOUT")

    input_group = parser.add_argument_group("Input")
    input_group.add_argument("--input-directory",
                             type=dir_path,
                             help="Directory where resources should be converted from. Resources that are not FHIR Terminology resources in JSON are skipped (XML is NOT supported)!"
                             )
    input_group.add_argument("files", nargs="*", type=argparse.FileType("r"),
                             help="You can list JSON files that should be converted, independent of the input dir parameter. XML is NOT supported")

    args = parser.parse_args()
    log = configure_logging(args.log_level, args.log_file)
    if args.patch_directory == None:
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
        if arg in ["oauth_token", "bearer_authentication", "basic_authentication"]:
            log.info(f" - {arg} : **SECRET**")
            continue
        log.info(f" - {arg} : {getattr(args, arg)}")
    input("Press any key to continue.")
    return args


def get_oauth_service(args: Namespace) -> Optional[OAuth2Service]:
    required_args = [
        args.oauth_authorize,
        args.oauth_token,
        args.oauth_client_id,
        args.oauth_redirect
    ]
    none_args = [x for x in required_args if x is None]
    present_args = [x for x in required_args if x is not None]
    if None in none_args and any(present_args):
        log.error("OAuth2 was not configured correctly. All arguments are required, except for client ID, which can be entered interactively if it is missing.")
        exit(1)
    elif len(none_args) == len(required_args):
        log.debug("Not using OAuth2")
        return None
    if args.oauth_client_secret is None:
        client_secret = inquirer.text("OAuth2 Client Secret")
        args.oauth_client_secret = client_secret
    service = OAuth2Service(
        name="oauth2",
        access_token_url=args.oauth_token,
        authorize_url=args.oauth_authorize,
        client_id=args.oauth_client_id,
        client_secret=args.oauth_client_secret
    )
    return service


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

        except Exception as e:
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


def request_oauth_token(session, cert, args) -> EncapsulatedOAuth2Token:
    state = uuid4()
    nonce = uuid4()
    scope = "openid"
    code_verifier: str
    code_challenge: str
    auth_params = {
        "redirect_uri": args.oauth_redirect,
        "response_type": "code",
        "state": str(state),
        "nonce": str(nonce),
        "scope": scope,
        "response_mode": "query"
    }
    if args.oauth_pkce:
        code_verifier, code_challenge = pkce.generate_pkce_pair()

        auth_params.update({
            'code_challenge_method': "S256",
            "code_challenge": code_challenge,
        })
    auth_url = oauth_service.get_authorize_url(**auth_params)
    log.warning(
        f"Please visit the authentication URL in the browser. You may need to disable the URL handler for the callback URL in the browser")
    log.warning(
        "You will need to copy the resulting URL from the browser and paste it into the dialog below")
    print(auth_url)
    auth_code = input("Authentication code? ").strip()
    # auth_code = inquirer.text("Enter the code parameter of the callback here")

    if "code=" in auth_code:
        log.debug("Parsing returned URL to get the code")
        o = urlparse(auth_code)
        auth_code = parse_qs(o.query)["code"][0]
    auth_params = {
        "code": auth_code,
        'grant_type': 'authorization_code',
        'redirect_uri': args.oauth_redirect
    }
    if args.oauth_pkce:
        auth_params.update({
            "code_verifier": code_verifier
        })

    try:
        client_auth = HTTPBasicAuth(
            args.oauth_client_id, args.oauth_client_secret)
        requested_at = datetime.now()
        oauth_response = session.post(
            args.oauth_token, data=auth_params, auth=client_auth).json()
        oauth_credential = EncapsulatedOAuth2Token(oauth_response,
                                                   args.oauth_token,
                                                   client_auth,
                                                   cert,
                                                   log,
                                                   requested_at)
        log.info("Successfully authorized using OAuth2")
        return oauth_credential
    except Exception:
        log.exception("Error obtaining OAuth2 Token")
        exit(1)


def upload_resources(args: Namespace,
                     sorted_resources: List[Dict[str, Union[NamingSystem, CodeSystem, ValueSet, ConceptMap]]],
                     oauth_service: Optional[OAuth2Service],
                     max_tries: int = 10,):
    base = urlparse(args.endpoint.rstrip('/') + "/")
    log.info("\n" * 2)
    log.info("##########")
    log.info(f"Uploading resources to {base.geturl()}...")
    session = requests.session()
    oauth_credential: Optional[EncapsulatedOAuth2Token] = None
    cert = None
    if args.cert != None:
        if "|" in args.cert:
            public, private = tuple([q.strip() for q in args.cert.split('|')])
            if not os.path.isfile(public) and os.access(public, os.R_OK):
                log.error(f"public key at {public} is not readable")
                exit(1)
            if not os.path.isfile(private) and os.access(private, os.R_OK):
                log.error(f"private key at {private} is not readable")
                exit(1)
            cert = (public, private)
            log.info(f"Using public / private key at: {cert}")
        else:
            if not os.path.isfile(args.cert) and os.access(args.cert, os.R_OK):
                log.error(f"combined key at {args.cert} is not readable")
                exit(1)
            cert = args.cert
            log.info(f"Using combined key at: {args.cert}")
            session.cert = cert

    if oauth_service is not None:
        oauth_credential = request_oauth_token(session, cert, args)
    elif args.basic_authentication is not None or args.bearer_authentication is not None:
        auth = f"Basic {args.basic_auth}" if args.bearer_authentication is None else f"Bearer {args.bearer_authentication}"
        log.debug(f"Using auth header: '{auth[:10]}...'")
        session.headers.update(
            {"Authorization": auth})
    session.headers.update({
        "Accept": "application/json"
    })

    if getproxies():
        session.proxies = getproxies()
        log.info(f"Using proxy: {getproxies()}")

    if args.cert != None:
        session.cert = cert

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
                if oauth_credential is not None:
                    if not oauth_credential.apply_authorization(session):
                        log.warning("Re-authorization is required")
                        oauth_credential = request_oauth_token(
                            session, cert, args)
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
                                filename, res, count_uploads, args.patch_directory)
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
                                filename, res, count_uploads, args.patch_directory, manual=True)
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


def edit_file(filename: str, resource: Union[NamingSystem, CodeSystem, ValueSet, ConceptMap], count_uploads: int, patch_directory: str, manual: Boolean = False):
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
            if patch_directory != None:
                patch_filename = os.path.join(
                    patch_directory, f"{raw_filename}.patch")
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
                    patch_directory, f"{raw_filename}.edited")
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
    oauth_service = get_oauth_service(args)
    files = gather_files(args)
    valid_resources = validate_files(args, files)
    sorted_resources = sort_resources(valid_resources)
    upload_resources(args, sorted_resources, oauth_service)
    log.info("We are done!")
