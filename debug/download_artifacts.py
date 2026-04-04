#!/usr/bin/env python3
"""Download stage artifacts from S3 for recent diarization jobs."""

import subprocess
from pathlib import Path

BUCKET = "dalston-artifacts-178457246645"
REGION = "eu-west-2"
OUT_DIR = Path(__file__).parent / "artifacts"

JOBS = {
    "6800aa1a-6ca5-4f9f-b688-2f29f264e585": {
        "prepare": "f4f39cb1-fd07-4f0a-b3fb-75dbadf3f202",
        "transcribe": "d0de4b07-4331-4abc-913a-84719886d5e6",
        "diarize": "2a43ad51-0a33-4b4e-81a2-7095d9ac5c8c",
    },
    "24200b0b-70bf-43eb-8eef-df19207ab7db": {
        "prepare": "afbc6555-1b3d-4f23-a781-fd135a5b698a",
        "transcribe": "ec7a0d4f-60fe-4d14-8edb-5bdc263b8523",
        "diarize": "e61d2d91-1a15-4cc4-b154-6f5827e7a33c",
    },
    "0340a612-ef1a-41b2-9339-275eaa624e6d": {
        "prepare": "62b07f4b-7273-4935-a558-b4062f1fb20e",
        "transcribe": "6aa4234f-7bb8-49a8-af98-5417ea658358",
        "diarize": "0308966f-87f4-469b-8429-472f2f22c745",
    },
    "c9aab204-5f4e-4496-a53d-cbf8da80420b": {
        "prepare": "a82aba6d-c078-49c5-9d39-c61fd606897f",
        "transcribe": "4e7bafac-7b62-46f9-9ae6-bacd7b5dc98d",
        "diarize": "867810c8-9bd7-4349-9980-842c7c04eeaf",
    },
    "3fd28f83-4583-4562-9d6f-d191bb92ec11": {
        "prepare": "ae6a468c-a5e3-4bee-8bee-3989316b8bb5",
        "transcribe": "55720e43-bc58-4c9c-b176-127ba1b8b00b",
        "diarize": "9b894e21-0d84-466e-8fe7-506894aab3f2",
    },
    "87abb8a8-58a4-43c1-a00d-083a6258830a": {
        "prepare": "afb3712e-86a6-42c8-ae5e-bf126d67607e",
        "transcribe": "fd8b4b86-10d0-48c1-ba1b-4ef71a257d98",
        "diarize": "67f2d5d1-c054-4d23-be2a-0335609ae431",
    },
    "01779092-628c-49a6-b6b6-01cbf1441263": {
        "prepare": "29d095d8-313f-4bf6-b464-46b8f76b26b8",
        "transcribe": "31b04c33-95f8-4cee-8dad-6f7ef24d0d8d",
        "diarize": "c712dd98-400d-4c66-9236-4794aea4fcd9",
    },
    "0917a88b-1c5c-4ac9-887e-73ab15b0a553": {
        "prepare": "b61369fd-1559-4c9e-ac8c-d6a54d4ffbe3",
        "transcribe": "1c8a85e1-4c9a-4bac-88f3-865afd686859",
        "diarize": "e3745ebe-fb86-4131-92c9-981811ac9af3",
    },
    "bd3b8360-553b-4109-a43b-4e9186e34911": {
        "prepare": "c1e35e5a-87c3-4184-b4f6-7267e6978ba4",
        "transcribe": "eff8861d-0922-4f33-837a-3aa69af9674b",
        "diarize": "c28ef028-cc4c-4a2c-9572-f1bc4cd97bac",
    },
    "462a7ff4-da0e-43d3-8506-73bf52dc68c4": {
        "prepare": "9ac94873-5cf9-4fae-8bff-7e20bf4ac541",
        "transcribe": "4a7bf13f-6c5d-4d49-b6b1-6ff6cea5d390",
        "diarize": "5db81b51-a91e-4e9d-9921-0ee7bec2a2bf",
    },
}


def s3_cp(s3_path: str, local_path: Path) -> bool:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["aws", "s3", "cp", s3_path, str(local_path), "--region", REGION],
        capture_output=True,
    )
    return result.returncode == 0


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for job_id, tasks in JOBS.items():
        job_dir = OUT_DIR / job_id
        print(f"=== Job {job_id} ===")

        # transcript.json
        ok = s3_cp(
            f"s3://{BUCKET}/jobs/{job_id}/transcript.json",
            job_dir / "transcript.json",
        )
        print(f"  transcript.json {'OK' if ok else 'MISSING'}")

        # Stage outputs
        for stage, task_id in tasks.items():
            prefix = f"s3://{BUCKET}/jobs/{job_id}/tasks/{task_id}"
            stage_dir = job_dir / stage

            ok = s3_cp(f"{prefix}/response.json", stage_dir / "response.json")
            print(f"  {stage}/response.json {'OK' if ok else 'MISSING'}")

            ok = s3_cp(f"{prefix}/request.json", stage_dir / "request.json")
            print(f"  {stage}/request.json {'OK' if ok else 'MISSING'}")

        print()

    print(f"Done. {len(JOBS)} jobs downloaded to {OUT_DIR}/")


if __name__ == "__main__":
    main()
