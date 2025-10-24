import json
import boto3
import os
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

# Globals
INDEX_NAME = os.environ.get('OPENSEARCH_INDEX_NAME', 'nexus')

# OpenSearch configuration
host = os.environ['OPENSEARCH_ENDPOINT'].replace('https://', '')
region = os.environ.get('AWS_REGION', 'us-east-1')
credentials = boto3.Session().get_credentials()
auth = AWSV4SignerAuth(credentials, region, 'aoss')

opensearch_client = OpenSearch(
    hosts=[{'host': host, 'port': 443}],
    http_auth=auth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30
)


def handler(event, context):
    """List all documents indexed in OpenSearch"""
    print(f"Received event: {json.dumps(event)}")
    
    try:
        # Check if index exists
        if not opensearch_client.indices.exists(index=INDEX_NAME):
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'documents': [],
                    'count': 0,
                    'message': 'No documents indexed yet'
                })
            }
        
        # Aggregate documents by filename
        response = opensearch_client.search(
            index=INDEX_NAME,
            body={
                "size": 0,
                "aggs": {
                    "documents": {
                        "terms": {
                            "field": "filename",
                            "size": 1000
                        },
                        "aggs": {
                            "pages": {
                                "stats": {
                                    "field": "page"
                                }
                            }
                        }
                    }
                }
            }
        )
        
        documents = []
        for bucket in response['aggregations']['documents']['buckets']:
            documents.append({
                'filename': bucket['key'],
                'chunk_count': bucket['doc_count'],
                'page_count': int(bucket['pages']['max'])
            })
        
        # Sort by filename
        documents.sort(key=lambda x: x['filename'])
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'documents': documents,
                'count': len(documents)
            })
        }
        
    except Exception as e:
        print(f"Error listing documents: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }
