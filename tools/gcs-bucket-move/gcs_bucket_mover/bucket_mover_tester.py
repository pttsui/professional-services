# Copyright 2018 Google LLC. All rights reserved. Licensed under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License
# is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing permissions and limitations under
# the License.
#
# Any software provided by Google hereunder is distributed "AS IS", WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, and is not intended for production use.
"""This is a testing method designed to be run from the command line.

It is meant to be run on a test bucket so you can confirm everything works before attempting to
move an actual production bucket. It will also attempt to delete the bucket from both the source
and target projects before it starts so that it can be run repeatedly.
"""

from __future__ import absolute_import

from faker import Faker
from yaspin import yaspin

from google.cloud import exceptions
from google.cloud import storage

from gcs_bucket_mover import configuration
from gcs_bucket_mover import bucket_mover_service

BUCKET_LOCATION = 'us'
CUSTOM_ROLE_NAME = 'projects/my_source_prj/roles/TestRole'
DEFAULT_KMS_KEY_NAME = 'projects/my_source_prj/locations/global/keyRings/my_ring/cryptoKeys/my_key'
EMAIL_FOR_IAM = 'test@google.com'
LOGGING_BUCKET = 'source_bucket-logs'
LOGGING_PREFIX = 'prefix-'
STORAGE_CLASS = 'STANDARD'
TOPIC_NAME = 'my_topic'


def set_up_test_bucket(conf):
    """Sets up the test bucket, adds objects and assigns various settings.

    It makes sure none of the buckets already exist, and then runs the main bucket mover service.

    Args:
        conf: the argparser parsing of command line options
    """

    #Load the environment config values set in config.sh and create the storage clients.
    config = configuration.Configuration(conf)

    with yaspin(text='TESTING: Cleanup source bucket') as spinner:
        try:
            _check_bucket_exists_and_delete(
                spinner, config.source_storage_client, conf.bucket_name,
                conf.source_project)
        except exceptions.Forbidden:
            try:
                #Maybe the bucket already exists in the target project.
                _check_bucket_exists_and_delete(
                    spinner, config.target_storage_client, conf.bucket_name,
                    conf.target_project)
            except exceptions.Forbidden:
                spinner.write('TESTING: Not allowed to access bucket {}'.format(
                    conf.bucket_name))
                spinner.fail('X')
                raise SystemExit()

        source_bucket = create_bucket(config.source_storage_client,
                                      conf.bucket_name)
        spinner.write(
            '{} TESTING: Bucket {} created in source project {}'.format(
                bucket_mover_service.CHECKMARK, conf.bucket_name,
                conf.source_project))

    _upload_blobs(source_bucket)

    with yaspin(text='TESTING: Cleanup target bucket') as spinner:
        _check_bucket_exists_and_delete(spinner, config.target_storage_client,
                                        config.temp_bucket_name,
                                        conf.target_project)


def create_bucket(storage_client, bucket_name):
    """Creates the test bucket.

    Also sets up lots of different bucket settings to make sure they can be moved.

    Args:
        storage_client: The storage client object used to access GCS
        bucket_name: The name of the bucket to create

    Returns:
        The bucket object that has been created in GCS
    """

    bucket = storage.Bucket(client=storage_client, name=bucket_name)
    # Requester pays
    bucket.requester_pays = False
    # CORS
    policies = bucket.cors
    policies.append({'origin': ['/foo']})
    policies[0]['maxAgeSeconds'] = 3600
    bucket.cors = policies
    # KMS Key
    #bucket.default_kms_key_name = DEFAULT_KMS_KEY_NAME
    # Labels
    bucket.labels = {'colour': 'red', 'flavour': 'cherry'}
    # Object Lifecycle Rules
    bucket.lifecycle_rules = [{
        "action": {
            "type": "Delete"
        },
        "condition": {
            "age": 365
        }
    }]
    # Location
    bucket.location = BUCKET_LOCATION
    # Storage Class
    bucket.storage_class = STORAGE_CLASS
    # File Versioning
    # Setting this to True means we can't delete a non-empty bucket with the CLI in one
    # bucket.delete command
    bucket.versioning_enabled = False
    # Access Logs
    bucket.enable_logging(LOGGING_BUCKET, LOGGING_PREFIX)

    bucket.create()

    # IAM Policies
    policy = bucket.get_iam_policy()
    #print(json.dumps(policy.to_api_repr(), indent=4, sort_keys=True))
    #policy[CUSTOM_ROLE_NAME].add('user:' + EMAIL_FOR_IAM)
    policy['roles/storage.admin'].add('user:' + EMAIL_FOR_IAM)
    bucket.set_iam_policy(policy)
    # ACLs
    bucket.acl.user(EMAIL_FOR_IAM).grant_read()
    bucket.acl.save()
    # Default Object ACL
    bucket.default_object_acl.user(EMAIL_FOR_IAM).grant_read()
    bucket.default_object_acl.save()

    bucket.update()

    # Bucket Notification
    notification = storage.notification.BucketNotification(
        bucket,
        TOPIC_NAME,
        custom_attributes={'myKey': 'myValue'},
        event_types=['OBJECT_FINALIZE', 'OBJECT_DELETE'],
        payload_format='JSON_API_V1')
    notification.create()

    return bucket


def _check_bucket_exists_and_delete(spinner, storage_client, bucket_name,
                                    project_name):
    """Checks if the bucket exists and delete it.

    If it already exists, prompt the user to make sure they want to delete it and everything in
    it.

    Args:
        spinner: The spinner displayed in the console
        storage_client: The storage client object used to access GCS
        bucket_name: The name of the bucket to check if it exists
        project_name: The name of the project to check the bucket exists in

    Raises:
        SystemExit: If the bucket already exists and the user does not choose to delete it
    """

    bucket = storage.Bucket(
        client=storage_client, name=bucket_name, user_project=project_name)
    if bucket.exists():
        spinner.hide()
        answer = raw_input(
            '\nWARNING!!! Bucket {} already exists in project {}\nType YES to confirm you want to'
            ' delete it: '.format(bucket_name, project_name))
        spinner.show()
        if answer != 'YES':
            spinner.fail('X')
            raise SystemExit()
        spinner.write('')
        bucket.delete(force=True)
        spinner.write('{} TESTING: Bucket {} deleted from project {}'.format(
            bucket_mover_service.CHECKMARK, bucket_name, project_name))


def _upload_blobs(bucket):
    """Uploads some random text files to the bucket.

    Args:
        bucket: The bucket object to upload blobs to
    """

    with yaspin(text='TESTING: Uploading 5 random txt files') as spinner:
        fake = Faker()
        for number in xrange(5):
            blob = bucket.blob(fake.file_name(extension='txt'))  # pylint: disable=no-member
            blob.metadata = {'customKey': 'myFakeValue' + str(number)}
            blob.upload_from_string(fake.text())  # Generator is dynamic. pylint: disable=no-member
    spinner.ok(bucket_mover_service.CHECKMARK)
