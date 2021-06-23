import json
import sys

if __name__ == "__main__":
    suffix = sys.argv[1].strip()
    print(f"using suffix '{suffix}'")
    files = sys.argv[2:]
    for file in files:
        print(f"file {file}")
        with open(file, "r+") as jf:
            json_contents = json.load(jf)
            try:
                current_id = json_contents["id"]
                print(f"  id: {current_id} (length {len(current_id)})")
                allowed_length = 64 - len(suffix) - 1
                trim_id = current_id[:allowed_length]
                new_id = f"{trim_id}_{suffix}"
                print(f"  new id: {new_id} (length: {len(new_id)})")
                json_contents["id"] = new_id
                jf.seek(0)
                jf.truncate()
                json.dump(json_contents, jf, indent=2)
            except KeyError:
                print("  no ID in resource")
                continue

