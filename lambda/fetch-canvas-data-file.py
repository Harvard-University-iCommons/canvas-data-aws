import json
import logging
from io import BytesIO
from pprint import pprint

import boto3
import requests
from smart_open import open

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):

    file_url = event['file_url']
    s3_bucket = event['s3_bucket']
    key = event['key']

    chunk_size = 1024*1024*8

    logger.info('fetching {} to {}'.format(file_url, key))

    s3 = boto3.client('s3')
    obj_list = s3.list_objects_v2(Bucket=s3_bucket, Prefix=key)

    if obj_list.get('KeyCount', 0) > 0:
        logger.warn('trying to download {} but it already exists -- skipping'.format(key))
        return({
            'message': 'key {} already exists - skipping'.format(key)
        })

    with open('s3://{}/{}'.format(s3_bucket, key), 'wb', ignore_ext=True) as fout:
        with requests.get(file_url, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk: # filter out keep-alive new chunks
                    fout.write(chunk)

    return {
        'statusCode': 200,
    }
