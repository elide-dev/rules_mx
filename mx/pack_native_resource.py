"""Pack one target executable using the mx FileListArchiver contract."""

from hashlib import sha256
from pathlib import Path
import sys

from pack_jar import write_jar


def main(output, library, member, file_list, hash_entry):
    data = Path(library).read_bytes()
    digest = sha256(data)
    digest.update(member.encode())
    write_jar(
        output,
        {
            member: data,
            file_list: (member + " = rwxr-xr-x").encode(),
            hash_entry: digest.hexdigest().encode(),
        },
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
