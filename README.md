# FHIR Terminology Services Batch Upload

This script can be used to upload many HL7 FHIR Terminology resources to a FHIR Terminology Server (TS). It will validate the syntactic validity of the resources, but also consider the result of the upload. You will be able to edit the files in the terminal to react to errors. This will also generate patch files, so you can give feedback to the authors easily!

**⚠ Only JSON resources are supported by this script! You will need to convert resources to JSON if they are provided in XML! ⚠**

## Installation

This program was written in Python and requires some dependencies. It requires Python 3. Also, it was only tested on GNU/Linux and macOS systems. It will certainly run using Windows Subsystem for Linux, but probably not on Windows natively!

This programs was tested successfully using WSL (Ubuntu 20.04 LTS) and Python 3.8.10. It seemingly is *not* compatible with Python 3.6.x (the default of the WSL Ubuntu 18.04 LTS distribution). When in doubt, use at least Python 3.8).

In a terminal, create a new virtual environment for the dependencies:

```bash
cd fhir-ts-batch
python3 -m venv .venv
```

Now, activate the virtual environment:

```bash
source .venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

And view the help:

```bash
python upload-resources.py --help
```

```
usage: upload-resources.py [-h] --endpoint ENDPOINT
                           [--basic-authentication BASIC_AUTHENTICATION]
                           [--bearer-authentication BEARER_AUTHENTICATION]
                           [--oauth-authorize OAUTH_AUTHORIZE]
                           [--oauth-token OAUTH_TOKEN]
                           [--oauth-client-id OAUTH_CLIENT_ID]
                           [--oauth-client-secret OAUTH_CLIENT_SECRET]
                           [--oauth-redirect OAUTH_REDIRECT] [--oauth-pkce]
                           [--cert CERT] [--patch-directory PATCH_DIRECTORY]
                           [--log-level {NOTSET,DEBUG,INFO,WARNING,ERROR}]
                           [--log-file LOG_FILE]
                           [--input-directory INPUT_DIRECTORY]
                           [files ...]

optional arguments:
  -h, --help            show this help message and exit

Required:
  --endpoint ENDPOINT   The FHIR TS endpoint (default:
                        http://localhost:8080/fhir)

Authentication:
  --basic-authentication BASIC_AUTHENTICATION
                        An Basic authentication credential (default: None)
  --bearer-authentication BEARER_AUTHENTICATION
                        An Bearer authentication credential (default: None)
  --oauth-authorize OAUTH_AUTHORIZE
                        OAuth2 Authorization URL (default: None)
  --oauth-token OAUTH_TOKEN
                        OAuth Token URL (default: None)
  --oauth-client-id OAUTH_CLIENT_ID
                        OAuth Client ID (default: None)
  --oauth-client-secret OAUTH_CLIENT_SECRET
                        OAuth Client Secret (default: None)
  --oauth-redirect OAUTH_REDIRECT
                        Redirect URL for OIDC. Must be legal in the
                        authentication server configuration (default: None)
  --oauth-pkce          If provided, use PKCE for authentication (default:
                        False)
  --cert CERT           Provide a PKI keypair to use for mutual TLS
                        authentication. You can either provide a single file
                        path, containing both the public and private key
                        (often .pem) or two files, seperated by '|': first
                        public key (often .crt), then private key (often .key)
                        (default: None)

Traceability:
  --patch-directory PATCH_DIRECTORY
                        a directory where patches and modified files are
                        written to. Not required, but recommended! (default:
                        None)
  --log-level {NOTSET,DEBUG,INFO,WARNING,ERROR}
                        Log level (default: INFO)
  --log-file LOG_FILE   Filename where a log file should be written to. If not
                        provided, output will only be provided to STDOUT
                        (default: None)

Input:
  --input-directory INPUT_DIRECTORY
                        Directory where resources should be converted from.
                        Resources that are not FHIR Terminology resources in
                        JSON are skipped (XML is NOT supported)! (default:
                        None)
  files                 You can list JSON files that should be converted,
                        independent of the input dir parameter. XML is NOT
                        supported (default: None)
```

You can use those commands to provide the file you need.

## Invocation

A normal invocation of the program could look like this:

```bash
python upload-resources.py \
  --input-directory /home/user/Downloads/terminology-files \
  --patch-directory /home/user/Downloads/terminology-patches \
  --endpoint http://localhost:8080/fhir \
  --log-file /home/user/Downloads/terminology-upload.log
```

The script will print the command line arguments, and then validate the provided files. 

**⚠ Only JSON resources are supported by this script! You will need to convert resources to JSON if they are provided in XML! ⚠**

Next, the script will upload all the resources to the provided TS. First `NamingSystem`, then `CodeSystem`, `ValueSet` and `ConceptMap`.

If the TS returns an error, you will get options:

```
INFO     uploading (try #1/10)
INFO     received status code 422
ERROR    This status code means an error occurred.
ERROR    FHIR OperationOutcome Issue: ['{"code": "invalid", "details": "text": "Code System concept includes a property value for category but the Code System does not define this property."}, "severity": "error"']

[?] What should we do?: Edit (using your editor from $EDITOR)
 > Edit (using your editor from $EDITOR)
   Ignore (continue with the next resource)
   Retry (because you have changed/uploaded something else)
```

Select one of the options to continue.

1. Edit: This opens the file for editing in your Terminal, using the editor provided in the `$EDITOR` environment variable. You can fix obvious errors this way. When closing the file, a patch and the modified file will be written to the `patch-directory` you specified using the command line arguments. You can use the patches to provide your fixes to the maintainers of the resources. Apply the patches to the original file using `patch original-file.json patch-file.patch` in order (ascending, 'manual' after the patches without 'manual').
2. Ignore: skip this file and continue.
3. Retry: do it again! Use this e.g. if you uploaded a ValueSet that requires another CodeSystem not contained in the main directory.

## ValueSet validation

The main additional feature of this scripts is the automatic expansion of ValueSets to make sure they work appropriately. Besides calling the validation operation and checking the HTTP status code, this routine carries out the following checks:

* if the `expansion.contains` attribute is empty, i.e. the expansion is missing, this is an error.
* The number of referenced concepts is printed to the screen/log for manual verification.
* The code system URLs referenced in the definition of the VS each have to provide at least concept within the ValueSet expansion. E.g. a ValueSet that references `http://loinc.org` and `http://snomed.info/sct` in `compose.include` is considered to invalid if there is no LOINC concept in the expansion -- this indicates that either the implicit specification of the VS is nonsensical, or there is a version mismatch.

## OAuth2 Authentication

There are a number of options required for authenticating to a terminology server using OAuth2, using the Authentication Code flow.
You will need to provide the following parameters:
* `--oauth-authorize`: the authentication URL for the OAuth2 server
* `--oauth-token`: the URL where tokens can be obtained by trading in access codes.
* `--oauth-redirect`: the redirection URL that is to be used, e.g. `https://oauth.pstmn.io/v1/callback`
* `--oauth-client-id`: the client ID to use for authentication

The client secret can either be provided via the command line, or entered interactively, so that is does not occur in the logs or the command line history.

Additionally, there is a flag `--oauth-pkce` that enables [OAuth2 PKCE](https://oauth.net/2/pkce/) using SHA256.

The software will generate an authentication url that you need to open in a browser. After logging in, the authentication server will redirect to the redirection URL, and append to `code` in an URL parameter. You will need to make sure that the redirection is

1. configured and allowed in the authentication server
2. valid and resolvable
3. not opened in other software, like Postman

If all those conditions are satisfied, you can copy the entire redirection URL from the browser to the input field, and the software will request an access token using the provided code.

You might also try to use the `--bearer-auth` parameter, and obtain the access key in some other fashion, instead of relying on OAuth2.

## PKI Client Authentication

If the server you are talking to requires the presentation of a key pair within a certain Public Key Infrastructure, you can use the `--cert` parameter.

Either provide a single file with the public and private key (ideally using the entire public key chain), or two files (one for the public key chain, one with the private key). In case of two files, separate the paths using `|`.
