# FHIR Terminology Services Batch Upload

This script can be used to upload many HL7 FHIR Terminology resources to a FHIR Terminology Server (TS). It will validate the syntactic validity of the resources, but also consider the result of the upload. You will be able to edit the files in the terminal to react to errors. This will also generate patch files, so you can give feedback to the authors easily!

**⚠ Only JSON resources are supported by this script! You will need to convert resources to JSON if they are provided in XML! ⚠**

## Installation

This program was written in Python and requires some dependencies. It requires Python 3. Also, it was only tested on GNU/Linux and macOS systems. It will certainly run using Windows Subsystem for Linux, but probably not on Windows natively!

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
usage: upload-resources.py [-h] [--endpoint ENDPOINT]
                           [--authentication-credential AUTHENTICATION_CREDENTIAL]
                           [--authentication-type {Bearer,Basic}]
                           [--input-directory INPUT_DIRECTORY]
                           [--patch-directory PATCH_DIRECTORY]
                           [--log-level {NOTSET,DEBUG,INFO,WARNING,ERROR}]
                           [--log-file LOG_FILE] [--cert CERT]
                           [files ...]

positional arguments:
  files                 You can list JSON files that should be converted,
                        independent of the input dir parameter. XML is NOT
                        supported (default: None)

optional arguments:
  -h, --help            show this help message and exit
  --endpoint ENDPOINT   The FHIR TS endpoint (default:
                        http://localhost:8080/fhir)
  --authentication-credential AUTHENTICATION_CREDENTIAL
                        An authentication credential. If blank, no
                        authentication will be presented to the TS. (default:
                        None)
  --authentication-type {Bearer,Basic}
                        The type of authentication credential (default:
                        Bearer)
  --input-directory INPUT_DIRECTORY
                        Directory where resources should be converted from.
                        Resources that are not FHIR Terminology resources in
                        JSON are skipped (XML is NOT supported)! (default:
                        None)
  --patch-directory PATCH_DIRECTORY
                        a directory where patches and modified files are
                        written to. Not required, but recommended! (default:
                        None)
  --log-level {NOTSET,DEBUG,INFO,WARNING,ERROR}
                        Log level (default: INFO)
  --log-file LOG_FILE   Filename where a log file should be written to. If not
                        provided, output will only be provided to STDOUT
                        (default: None)
  --cert CERT           Provide a PKI keypair to use for mutual TLS
                        authentication. You can either provide a single file
                        path, containing both the public and private key
                        (often .pem) or two files, seperated by '|': first
                        public key (often .crt), then private key (often .key)
                        (default: None)
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
