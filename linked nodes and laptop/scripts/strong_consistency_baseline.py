import json
from datetime import datetime, timezone

from pymongo import MongoClient, ReadPreference, ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


import argparse
from cluster_config import LOCAL, load_linked
parser = argparse.ArgumentParser()
parser.add_argument("--linked", action="store_true")
cluster = load_linked() if parser.parse_args().linked else LOCAL
URI = cluster.uri

# Create a replica-set-aware MongoDB client.
client = MongoClient(
    URI,
    **cluster.auth,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=3000,
    socketTimeoutMS=10000,
    retryWrites=False,
)

# Use a unique ID so repeated runs do not overwrite one another.
run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


# Print one JSON record for each test.
def report(property_name, passed, **details):
    record = {
        "run_id": run_id,
        "property": property_name,
        "passed": passed,
        **details,
    }
    print(json.dumps(record, default=str), flush=True)


# Majority write concern:
# Do not acknowledge the write until the replica set satisfies
# its voting-majority requirement.
majority_write = WriteConcern(
    w="majority",
    j=True,
    wtimeout=5000,
)

# This collection uses:
# - secondary-preferred reads;
# - majority read concern; and
# - majority write concern.
collection = client.lab.assignment_tests.with_options(
    read_preference=ReadPreference.SECONDARY_PREFERRED,
    read_concern=ReadConcern("majority"),
    write_concern=majority_write,
)

# A primary-reading view is useful for final verification.
primary_collection = client.lab.assignment_tests.with_options(
    read_preference=ReadPreference.PRIMARY,
    read_concern=ReadConcern("majority"),
    write_concern=majority_write,
)


# Confirm that the driver can discover the replica set.
ping_result = client.admin.command("ping")
hello_result = client.admin.command("hello")

report(
    "CONNECTION",
    ping_result.get("ok") == 1,
    primary=hello_result.get("primary"),
    replica_set=hello_result.get("setName"),
)


# Keep all causally dependent operations in one logical session.
with client.start_session(causal_consistency=True) as session:

    # ---------------------------------------------------------
    # TEST 1: READ-YOUR-WRITES
    # ---------------------------------------------------------

    ryw_key = f"ryw-{run_id}"

    written_document = collection.find_one_and_update(
        {"_id": ryw_key},
        {
            "$setOnInsert": {
                "created_by": "strong-baseline",
            },
            "$inc": {"version": 1},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
        session=session,
    )

    written_version = written_document["version"]

    observed_document = collection.find_one(
        {"_id": ryw_key},
        session=session,
    )

    observed_version = observed_document["version"]

    report(
        "READ_YOUR_WRITES",
        observed_version >= written_version,
        written_version=written_version,
        observed_version=observed_version,
    )

    # ---------------------------------------------------------
    # TEST 2: MONOTONIC READS
    # ---------------------------------------------------------

    mr_key = f"mr-{run_id}"

    collection.replace_one(
        {"_id": mr_key},
        {
            "_id": mr_key,
            "version": 1,
        },
        upsert=True,
        session=session,
    )

    first_read = collection.find_one(
        {"_id": mr_key},
        session=session,
    )

    first_version = first_read["version"]

    # Advance the document to another version.
    collection.update_one(
        {"_id": mr_key},
        {"$inc": {"version": 1}},
        session=session,
    )

    second_read = collection.find_one(
        {"_id": mr_key},
        session=session,
    )

    second_version = second_read["version"]

    report(
        "MONOTONIC_READS",
        second_version >= first_version,
        first_version=first_version,
        second_version=second_version,
    )

    # ---------------------------------------------------------
    # TEST 3: MONOTONIC WRITES
    # ---------------------------------------------------------

    mw_key = f"mw-{run_id}"

    # W1 must occur before W2.
    collection.update_one(
        {"_id": mw_key},
        {
            "$set": {
                "step1": True,
                "step1_sequence": 1,
            }
        },
        upsert=True,
        session=session,
    )

    # This is the client's second write.
    collection.update_one(
        {"_id": mw_key},
        {
            "$set": {
                "step2": True,
                "step2_sequence": 2,
            }
        },
        session=session,
    )

    mw_document = collection.find_one(
        {"_id": mw_key},
        session=session,
    )

    mw_passed = (
        mw_document.get("step1") is True
        and mw_document.get("step2") is True
        and mw_document.get("step1_sequence") == 1
        and mw_document.get("step2_sequence") == 2
    )

    report(
        "MONOTONIC_WRITES",
        mw_passed,
        observed_document=mw_document,
    )

    # ---------------------------------------------------------
    # TEST 4: WRITES-FOLLOW-READS
    # ---------------------------------------------------------

    source_key = f"source-{run_id}"
    derived_key = f"derived-{run_id}"

    collection.replace_one(
        {"_id": source_key},
        {
            "_id": source_key,
            "version": 1,
            "message": "source fact",
        },
        upsert=True,
        session=session,
    )

    source_document = collection.find_one(
        {"_id": source_key},
        session=session,
    )

    version_that_client_read = source_document["version"]

    # The following write depends on the preceding read.
    collection.replace_one(
        {"_id": derived_key},
        {
            "_id": derived_key,
            "derived_from": source_key,
            "from_version": version_that_client_read,
            "message": "created after reading the source",
        },
        upsert=True,
        session=session,
    )

    # Verify the surviving state using majority reads from the primary.
    verified_source = primary_collection.find_one(
        {"_id": source_key}
    )

    verified_derived = primary_collection.find_one(
        {"_id": derived_key}
    )

    wfr_passed = (
        verified_source is not None
        and verified_derived is not None
        and verified_source["version"]
        >= verified_derived["from_version"]
    )

    report(
        "WRITES_FOLLOW_READS",
        wfr_passed,
        version_read=version_that_client_read,
        derived_from_version=verified_derived["from_version"],
    )


client.close()
