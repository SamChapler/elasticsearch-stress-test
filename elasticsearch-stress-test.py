#!/usr/bin/python
#
# Stress test tool for elasticsearch
# Written by Roi Rav-Hon @ Logz.io (roi@logz.io)
#
# This fork has been modified to add some new features
# - user authentication
# - http and https support
# - Ability to hit multiple client nodes per cluster
# - Ability to specify ports for the ES cluster
#

import signal
import sys

# Using argparse to parse cli arguments
import argparse

# Import threading essentials
from threading import Lock, Thread, Condition, Event

# For randomizing
import string
from random import randint, choice

# To get the time
import time

# For misc
import sys

# For json operations
import json

# For https
import certifi

# For parsing the es_address argument
import re

# Try and import elasticsearch
try:
    from elasticsearch import Elasticsearch

except Exception as e:
    print("""Could not import elasticsearch...
Try: pip install elasticsearch
Error: {}""".format(e))
    sys.exit(1)

# Set a parser object
parser = argparse.ArgumentParser()

# Adds all params
parser.add_argument("--es_address", nargs='+', help="The address(es) of your cluster (no protocol), port optional)", required=True)
parser.add_argument("--indices", type=int, help="The number of indices to write to for each ip", required=True)
parser.add_argument("--documents", type=int, help="The number different documents to write for each ip", required=True)
parser.add_argument("--clients", type=int, help="The number of clients to write from for each ip", required=True)
parser.add_argument("--seconds", type=int, help="The number of seconds to run for each ip", required=True)
parser.add_argument("--use_https", action="store_true", help="Use https authentication. Otherwise use http")
parser.add_argument("--username", default='', help="The user, if required, to connect to the cluster")
parser.add_argument("--password", default='', help="The password, if required, to connect to the cluster")
parser.add_argument("--number-of-shards", type=int, default=3, help="Number of shards per index (default 3)")
parser.add_argument("--number-of-replicas", type=int, default=1, help="Number of replicas per index (default 1)")
parser.add_argument("--bulk-size", type=int, default=1000, help="Number of document per request (default 1000)")
parser.add_argument("--max-fields-per-document", type=int, default=100,
                    help="Max number of fields in each document (default 100)")
parser.add_argument("--max-size-per-field", type=int, default=1000, help="Max content size per field (default 1000")
parser.add_argument("--no-cleanup", default=False, action='store_true', help="Don't delete the indices upon finish")
parser.add_argument("--stats-frequency", type=int, default=30,
                    help="Number of seconds to wait between stats prints (default 30)")
parser.add_argument("--not-green", dest="green", action="store_false")
parser.set_defaults(green=True)

# Parse the arguments
args = parser.parse_args()

# Set variables from argparse output (for readability)
NUMBER_OF_INDICES = args.indices
NUMBER_OF_DOCUMENTS = args.documents
NUMBER_OF_CLIENTS = args.clients
NUMBER_OF_SECONDS = args.seconds
NUMBER_OF_SHARDS = args.number_of_shards
NUMBER_OF_REPLICAS = args.number_of_replicas
BULK_SIZE = args.bulk_size
MAX_FIELDS_PER_DOCUMENT = args.max_fields_per_document
MAX_SIZE_PER_FIELD = args.max_size_per_field
NO_CLEANUP = args.no_cleanup
STATS_FREQUENCY = args.stats_frequency
WAIT_FOR_GREEN = args.green
USER = args.username
PASSWORD = args.password
HTTPS = args.use_https

# timestamp placeholder
STARTED_TIMESTAMP = 0

# Placeholders
success_bulks = 0
failed_bulks = 0
total_size = 0
indices = []
documents = []
documents_templates = []
es = None  # Will hold the elasticsearch session

# Thread safe
success_lock = Lock()
fail_lock = Lock()
size_lock = Lock()
shutdown_event = Event()


# Helper functions
def increment_success():
    # First, lock
    success_lock.acquire()
    global success_bulks
    try:
        # Increment counter
        success_bulks += 1

    finally:  # Just in case
        # Release the lock
        success_lock.release()


def increment_failure():
    # First, lock
    fail_lock.acquire()
    global failed_bulks
    try:
        # Increment counter
        failed_bulks += 1

    finally:  # Just in case
        # Release the lock
        fail_lock.release()


def increment_size(size):
    # First, lock
    size_lock.acquire()

    try:
        # Using globals here
        global total_size

        # Increment counter
        total_size += size

    finally:  # Just in case
        # Release the lock
        size_lock.release()


def has_timeout(STARTED_TIMESTAMP):
    # Match to the timestamp
    if (STARTED_TIMESTAMP + NUMBER_OF_SECONDS) > int(time.time()):
        return False

    return True


# Just to control the minimum value globally (though its not configurable)
def generate_random_int(max_size):
    try:
        return randint(1, max_size)
    except Exception as e:
        print("Not supporting {0} as valid sizes! Error: {1}".format(max_size, e))
        sys.exit(1)


# Generate a random string with length of 1 to provided param
def generate_random_string(max_size):
    return ''.join(choice(string.ascii_lowercase) for _ in range(generate_random_int(max_size)))


# Create a document template
def generate_document():
    temp_doc = {}

    # Iterate over the max fields
    for _ in range(generate_random_int(MAX_FIELDS_PER_DOCUMENT)):
        # Generate a field, with random content
        temp_doc[generate_random_string(10)] = generate_random_string(MAX_SIZE_PER_FIELD)

    # Return the created document
    return temp_doc


def fill_documents(documents_templates):
    # Generating 10 random subsets
    for _ in range(10):

        # Get the global documents
        global documents

        # Get a temp document
        temp_doc = choice(documents_templates)

        # Populate the fields
        for field in temp_doc:
            temp_doc[field] = generate_random_string(MAX_SIZE_PER_FIELD)

        documents.append(temp_doc)


def client_worker(es, indices, STARTED_TIMESTAMP):
    # Running until timeout
    while (not has_timeout(STARTED_TIMESTAMP)) and (not shutdown_event.is_set()):

        curr_bulk = ""

        # Iterate over the bulk size
        for _ in range(BULK_SIZE):
            # Generate the bulk operation
            bulk_dict = {"index": {"_index": choice(indices), "_type": "stresstest"}}
            curr_bulk += "{0}\n".format(json.dumps(bulk_dict))
            curr_bulk += "{0}\n".format(json.dumps(choice(documents)))

        try:
            # Perform the bulk operation
            es.bulk(body=curr_bulk)

            # Adding to success bulks
            increment_success()

            # Adding to size (in bytes)
            increment_size(sys.getsizeof(str(curr_bulk)))

        except Exception:
            # Failed. incrementing failure
            increment_failure()


def generate_clients(es, indices, STARTED_TIMESTAMP):
    # Clients placeholder
    temp_clients = []

    # Iterate over the clients count
    for _ in range(NUMBER_OF_CLIENTS):
        temp_thread = Thread(target=client_worker, args=[es, indices, STARTED_TIMESTAMP])
        temp_thread.daemon = True

        # Create a thread and push it to the list
        temp_clients.append(temp_thread)

    # Return the clients
    return temp_clients


def generate_documents():
    # Documents placeholder
    temp_documents = []

    # Iterate over the clients count
    for _ in range(NUMBER_OF_DOCUMENTS):
        # Create a document and push it to the list
        temp_documents.append(generate_document())

    # Return the documents
    return temp_documents


def generate_indices(es):
    # Placeholder
    temp_indices = []

    # Iterate over the indices count
    for _ in range(NUMBER_OF_INDICES):
        # Generate the index name
        temp_index = generate_random_string(16)

        # Push it to the list
        temp_indices.append(temp_index)

        try:
            body = {"settings": {
                    "number_of_shards": NUMBER_OF_SHARDS,
                    "number_of_replicas": NUMBER_OF_REPLICAS}}
            # And create it in ES with the shard count and replicas
            es.indices.create(index=temp_index, body=body)

        except Exception:
            print("Could not create index. Is your cluster ok?")

    # Return the indices
    return temp_indices


def cleanup_indices(es, indices):
    # Iterate over all indices and delete those
    for curr_index in indices:
        try:
            # Delete the index
            es.indices.delete(index=curr_index, ignore=[400, 404])

        except Exception:
            print("Could not delete index: {0}. Continue anyway..".format(curr_index))


def print_stats(STARTED_TIMESTAMP):
    # Calculate elpased time
    elapsed_time = (int(time.time()) - STARTED_TIMESTAMP)

    # Calculate size in MB
    size_mb = total_size / 1024 / 1024

    # Protect division by zero
    if elapsed_time == 0:
        mbs = 0
    else:
        mbs = size_mb / float(elapsed_time)

    # Print stats to the user
    print("Elapsed time: {0} seconds".format(elapsed_time))
    print("Successful bulks: {0} ({1} documents)".format(success_bulks, (success_bulks * BULK_SIZE)))
    print("Failed bulks: {0} ({1} documents)".format(failed_bulks, (failed_bulks * BULK_SIZE)))
    print("Indexed approximately {0} MB which is {1:.2f} MB/s".format(size_mb, mbs))
    print("")


def print_stats_worker(STARTED_TIMESTAMP):
    # Create a conditional lock to be used instead of sleep (prevent dead locks)
    lock = Condition()

    # Acquire it
    lock.acquire()

    # Print the stats every STATS_FREQUENCY seconds
    while (not has_timeout(STARTED_TIMESTAMP)) and (not shutdown_event.is_set()):

        # Wait for timeout
        lock.wait(STATS_FREQUENCY)

        # To avoid double printing
        if not has_timeout(STARTED_TIMESTAMP):
            # Print stats
            print_stats(STARTED_TIMESTAMP)


def main():
    clients = []
    all_indecies = []

    # Set the timestamp
    STARTED_TIMESTAMP = int(time.time())

    for tmpaddress in args.es_address:
        print("")
        # Pull out port numbers if specified
        print("Parsing address string")
        try:
            # print tmpaddress
            esaddress = []
            tmplist = tmpaddress.split(',')
            es_port = -1
            # print("tmplist = {0}".format(tmplist))
            for address in tmplist:
                # print address
                regexresult = re.match("([a-zA-Z_0-9.-]+)\:(\d+)", address)
                if regexresult:
                    esaddress.append(regexresult.group(1))
                    if es_port > 0 and regexresult.group(2) != es_port:
                        print("Error: Ports in {0} don't match".format(tmplist))
                        sys.exit(1)
                    else:
                        es_port = regexresult.group(2)
                else:
                    esaddress.append(address)
                    es_port = 9200
        except Exception:
            print("Error parsing es_address string!")
            sys.exit(1)
        print("Starting initialization of {0}".format(esaddress))
        print("Port = {0}".format(es_port))
        try:
            # Initiate the elasticsearch session
            es = Elasticsearch(esaddress,
                               http_auth=(USER, PASSWORD),
                               port=es_port,
                               use_ssl=HTTPS,
                               verify_certs=HTTPS,
                               ca_certs=certifi.where(),
                               )

        except Exception as e:
            print("Could not connect to elasticsearch! Error: {}".format(e))
            sys.exit(1)

        # Generate docs
        documents_templates = generate_documents()
        fill_documents(documents_templates)

        print("Done!")
        print("Creating indices.. ")

        indices = generate_indices(es)
        all_indecies.extend(indices)

        try:
            # wait for cluster to be green if nothing else is set
            if WAIT_FOR_GREEN:
                es.cluster.health(wait_for_status='green', master_timeout='600s', timeout='600s')
        except Exception:
            print("Cluster timeout....")
            print("Cleaning up created indices.. "),

            cleanup_indices(es, indices)
            continue

        print("Generating documents and workers.. ")  # Generate the clients
        clients.extend(generate_clients(es, indices, STARTED_TIMESTAMP))

        print("Done!")


    print("Starting the test. Will print stats every {0} seconds.".format(STATS_FREQUENCY))
    print("The test would run for {0} seconds, but it might take a bit more "
          "because we are waiting for current bulk operation to complete. \n".format(NUMBER_OF_SECONDS))

    # Run the clients!
    map(lambda thread: thread.start(), clients)

    # Create and start the print stats thread
    stats_thread = Thread(target=print_stats_worker, args=[STARTED_TIMESTAMP])
    stats_thread.daemon = True
    stats_thread.start()

    for c in clients:
        while c.is_alive():
            try:
                c.join(timeout=0.1)
            except KeyboardInterrupt:
                print("")
                print("Ctrl-c received! Sending kill to threads...")
                shutdown_event.set()

                # set loop flag true to get into loop
                flag = True
                while flag:
                    # sleep 2 secs that we don't loop to often
                    time.sleep(2)
                    # set loop flag to false. If there is no thread still alive it will stay false
                    flag = False
                    # loop through each running thread and check if it is alive
                    for t in c.enumerate():
                        # if one single thread is still alive repeat the loop
                        if t.isAlive():
                            flag = True

                print("Cleaning up created indices.. "),
                cleanup_indices(es, all_indecies)

    print("\nTest is done! Final results:")
    print_stats(STARTED_TIMESTAMP)

    # Cleanup, unless we are told not to
    if not NO_CLEANUP:
        print("Cleaning up created indices.. "),

        cleanup_indices(es, all_indecies)

        print("Done!")  # # Main runner


try:
    main()

except Exception as e:
    print("Got unexpected exception. probably a bug, please report it.")
    print("")
    print(e.message)
    print("")
    sys.exit(1)
