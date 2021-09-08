import json
import logging
import os

import boto3
from canvas_data.api import CanvasDataAPI

logger = logging.getLogger()
logger.setLevel(os.environ.get('log_level', logging.INFO))


def lambda_handler(event, context):
    logger.debug('Starting Canvas Data sync')

    dry_run = True if os.environ.get('dry_run', '').lower() == 'true' else False

    sm  = boto3.client('secretsmanager')
    if os.environ.get('api_sm_id'):
        res = sm.get_secret_value(SecretId=os.environ['api_sm_id'])
        api_sm = json.loads(res['SecretString'])
        api_key, api_secret = api_sm['api_key'], api_sm['api_secret']
    else:
        api_key = os.environ['api_key']
        api_secret = os.environ['api_secret']

    fetch_function_name = os.environ.get('fetch_function_name')

    database = os.environ.get('database_name', 'canvasdata')
    s3_prefix = 'raw_files/'

    s3_bucket = os.environ['s3_bucket']
    sns_topic = os.environ['sns_topic']

    s3 = boto3.resource('s3')
    lmb = boto3.client('lambda')
    sns = boto3.client('sns')

    # get the list of the current objects in the s3 bucket
    b = s3.Bucket(s3_bucket)
    existing_keys = []
    existing_objects = b.objects.filter(Prefix=s3_prefix)
    for o in existing_objects:
        existing_keys.append(o.key)

    # now get all of the current files from the api
    cd = CanvasDataAPI(api_key=api_key, api_secret=api_secret)
    result_data = cd.get_sync_file_urls()
    files = result_data['files']

    numfiles = len(files)

    fetched_files = 0
    skipped_files = 0
    removed_files = 0

    reinvoke = False

    for f in files:

        key = '{}{}/{}'.format(s3_prefix, f['table'], f['filename'])

        if key in existing_keys:
            # we don't need to get this one
            # remove it from the existing_filenames so we don't delete it
            existing_keys.remove(key)
            logger.debug('skipping {}'.format(key))
            skipped_files += 1

        else:
            # we need to get it
            # call the other lambda asynchronously to download
            payload = {
                'file_url': f['url'],
                's3_bucket': s3_bucket,
                'key': key
            }
            if not dry_run:
                status = lmb.invoke(
                    FunctionName=fetch_function_name,
                    InvocationType='Event',
                    Payload=json.dumps(payload)
                )
                logger.info('fetching {} - status {}'.format(key, status))
                fetched_files += 1
            else:
                logger.info('would have fetched {}'.format(key))

        if context.get_remaining_time_in_millis() < 30000:
            # stop here and call this function again
            logger.info('this invocation has 30 seconds until timeout')
            logger.info('invoking another instance and exiting this one')
            reinvoke = True
            status = lmb.invoke(
                FunctionName=context.function_name,
                InvocationType='Event',
                Payload=json.dumps(event),
            )
            break

    tables_created = 0
    tables_updated = 0

    if not reinvoke:
        # now we need to delete any keys that remain in existing_keys
        s3c = boto3.client('s3')
        for old_key in existing_keys:
            if not dry_run:
                logger.info('removing old file {}'.format(old_key))
                s3c.delete_object(Bucket=s3_bucket, Key=old_key)
                removed_files += 1
            else:
                logger.info('would have removed old file {}'.format(old_key))

        # now update the Glue data catalog
        if not dry_run:
            schema = cd.get_schema()
            for tk in schema.keys():
                c_or_u = create_or_update_table(schema[tk], database, s3_bucket, s3_prefix)
                if c_or_u == 'created':
                    tables_created += 1
                elif c_or_u == 'updated':
                    tables_updated += 1


    logger.info('total number of files in the sync: {}'.format(numfiles))
    logger.info('fetched {} files'.format(fetched_files))
    logger.info('skipped {} files'.format(skipped_files))
    logger.info('removed {} old files'.format(removed_files))
    logger.info('tables created/updated: {}/{}'.format(tables_created, tables_updated))

    summary = {
        'total_files': numfiles,
        'fetched_files': fetched_files,
        'skipped_files': skipped_files,
        'removed_files': removed_files,
        'reinvoke': reinvoke,
        'tables_created': tables_created,
        'tables_updated': tables_updated,
    }

    sns.publish(
        TopicArn=sns_topic,
        Subject='Canvas Data sync complete',
        Message=json.dumps(summary, indent=4),
    )

    return summary


def get_column_type(column):
    # converts the column types returned by the Canvas Data API to Athena-compatible types
    raw_type = column['type']
    if raw_type in ['text', 'enum', 'guid']:
        return 'string'
    elif raw_type in ['varchar']:
        if column.get('length'):
            return 'varchar({})'.format(column['length'])
        else:
            return 'string'
    elif raw_type in ['double precision']:
        return 'double'
    elif raw_type in ['integer']:
        return 'int'
    elif raw_type in ['datetime']:
        return 'timestamp'
    else:
        return raw_type


def create_or_update_table(table_schema, database, s3_bucket, s3_prefix):

    table_desc = table_schema.get('description', '')[:254]

    table_input = {
        'Name': table_schema['tableName'],
        'Description': table_desc,
        'Parameters': {
            'compressionType': 'gzip',
            'delimiter': '\t',
            'classification': 'csv',
            'typeOfData': 'file'
        },
        'TableType': 'EXTERNAL_TABLE',
        'PartitionKeys': [],
        'StorageDescriptor': {
            'Location': 's3://{}/{}{}/'.format(s3_bucket, s3_prefix, table_schema['tableName']),
            'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
            'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
            'Compressed': True,
            'Parameters': {
                'compressionType': 'gzip',
                'delimiter': '\t',
                'classification': 'csv',
                'typeOfData': 'file'
            },
            'SerdeInfo': {
                'Name': 'LazySimpleSerDe',
                'SerializationLibrary': 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe',
                'Parameters': {
                    'field.delim': '\t'
                }
            }
        },
    }

    columns = []
    for column in table_schema['columns']:
        t = get_column_type(column)
        desc = column.get('description', '')[:254]

        c = {
            'Name': column['name'],
            'Type': t,
            'Comment': desc
        }
        columns.append(c)

    table_input['StorageDescriptor']['Columns'] = columns

    glue = boto3.client('glue')

    try:
        glue.create_table(
            DatabaseName=database,
            TableInput=table_input
        )
        logger.info('created Glue table {}.{}'.format(database, table_schema['tableName']))
        return 'created'
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(
            DatabaseName=database,
            TableInput=table_input
        )
        logger.info('updated Glue table {}.{}'.format(database, table_schema['tableName']))
        return 'updated'
