import json
import boto3
import os
import io
import urllib.parse
from PyPDF2 import PdfReader
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from openai import OpenAI

# Initialize clients
s3_client = boto3.client('s3')
secrets_client = boto3.client('secretsmanager')
_openai_client = None

# Globals
INDEX_NAME = os.environ.get('OPENSEARCH_INDEX_NAME', 'nexus')
OPENAI_SECRET_ARN = os.environ['OPENAI_SECRET_ARN']
OPENAI_EMBEDDING_MODEL = os.environ.get('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')
OPENAI_EMBEDDING_VECTOR_LENGTH = int(os.environ.get('OPENAI_EMBEDDING_VECTOR_LENGTH', '1536'))

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
    timeout=300
)


def get_openai_client():
    """Get OpenAI client with API key from Secrets Manager"""
    global _openai_client
    
    if _openai_client:
        return _openai_client
    
    try:
        print(f"Fetching OpenAI API key from {OPENAI_SECRET_ARN}")
        api_key = secrets_client.get_secret_value(SecretId=OPENAI_SECRET_ARN).get('SecretString')
        _openai_client = OpenAI(api_key=api_key)
        print("OpenAI client initialized successfully")
        return _openai_client
    except Exception as e:
        print(f"Error initializing OpenAI client: {e}")
        raise


def create_index_if_not_exists():
    """Create OpenSearch index with vector mapping for OpenAI embeddings"""
    try:
        if not opensearch_client.indices.exists(index=INDEX_NAME):
            index_body = {
                'settings': {
                    'index': {
                        'knn': True,
                        'knn.algo_param.ef_search': 512
                    }
                },
                'mappings': {
                    'properties': {
                        'vector': {
                            'type': 'knn_vector',
                            'dimension': OPENAI_EMBEDDING_VECTOR_LENGTH,
                            'method': {
                                'name': 'hnsw',
                                'space_type': 'cosinesimil',
                                'engine': 'nmslib'
                            }
                        },
                        'text': {'type': 'text'},
                        'filename': {'type': 'keyword'},
                        'page': {'type': 'integer'},
                        'chunk_id': {'type': 'keyword'}
                    }
                }
            }
            opensearch_client.indices.create(index=INDEX_NAME, body=index_body)
            print(f"Created index: {INDEX_NAME} with dimension {str(OPENAI_EMBEDDING_VECTOR_LENGTH)}")
        else:
            print(f"Index {INDEX_NAME} already exists")
    except Exception as e:
        print(f"Error creating index: {e}")
        raise


def extract_text_from_pdf(bucket, key):
    """Extract text from PDF file in S3 with cleaning"""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        pdf_content = response['Body'].read()
        
        pdf_reader = PdfReader(io.BytesIO(pdf_content))
        pages_text = []
        
        for page_num, page in enumerate(pdf_reader.pages, 1):
            text = page.extract_text()
            
            # Clean up the text
            text = text.strip()
            
            # Remove excessive whitespace but preserve paragraph structure
            import re
            text = re.sub(r' +', ' ', text)
            text = re.sub(r'\n{3,}', '\n\n', text)
            
            if text and len(text) > 100:
                pages_text.append({
                    'page': page_num,
                    'text': text
                })
                print(f"Page {page_num}: {len(text)} characters")
        
        print(f"Extracted text from {len(pages_text)} pages")
        return pages_text
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        raise


def chunk_text(text, chunk_size=1000, overlap=200):
    """Split text into overlapping chunks"""
    import re
    
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        # Get chunk
        end = start + chunk_size
        
        # If not at the end, try to break at sentence boundary
        if end < len(text):
            # Look for sentence endings in the last 200 chars
            search_start = max(start, end - 200)
            match = None
            for pattern in [r'[.!?]\s+', r'\n\n', r'\n']:
                matches = list(re.finditer(pattern, text[search_start:end]))
                if matches:
                    match = matches[-1]
                    break
            
            if match:
                end = search_start + match.end()
        
        chunk = text[start:end].strip()
        
        # Only add chunks with meaningful content
        if len(chunk) > 100 and len(chunk.split()) > 20:
            chunks.append(chunk)
        
        # Move start position with overlap
        start = end - overlap if end < len(text) else end
    
    print(f"Created {len(chunks)} chunks")
    return chunks


def get_embedding(text):
    """Get embedding from OpenAI"""
    if not text or not text.strip():
        raise ValueError("Cannot generate embedding for empty text")
    
    try:
        client = get_openai_client()
        
        if len(text) > 30000:
            text = text[:30000]
        
        response = client.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=text
        )
        
        embedding = response.data[0].embedding
        
        if not embedding or len(embedding) != OPENAI_EMBEDDING_VECTOR_LENGTH:
            raise ValueError(f"Invalid embedding: expected {str(OPENAI_EMBEDDING_VECTOR_LENGTH)} dimensions")
        
        return embedding
        
    except Exception as e:
        print(f"Error getting OpenAI embedding: {e}")
        raise


def process_pdf(bucket, key):
    """Main processing function"""
    create_index_if_not_exists()
    
    filename = urllib.parse.unquote_plus(key.split('/')[-1])
    print(f"Processing: {filename}")
    
    # Extract text from PDF
    pages_text = extract_text_from_pdf(bucket, key)
    
    documents_indexed = 0
    documents_failed = 0
    
    # Process each page
    for page_data in pages_text:
        page_num = page_data['page']
        text = page_data['text']
        
        print(f"\n=== Processing Page {page_num} ===")
        
        # Chunk the text
        chunks = chunk_text(text)
        print(f"Page {page_num}: {len(chunks)} chunks created")
        
        # Index each chunk
        for chunk_idx, chunk in enumerate(chunks):
            chunk_id = f"{filename}_p{page_num}_c{chunk_idx}"
            
            try:
                print(f"Indexing {chunk_id}...")
                
                # Generate embedding
                embedding = get_embedding(chunk)
                
                # Create document
                document = {
                    'vector': embedding,
                    'text': chunk,
                    'filename': filename,
                    'page': page_num,
                    'chunk_id': chunk_id
                }
                
                # Index document
                _ = opensearch_client.index(
                    index=INDEX_NAME,
                    body=document
                )
                
                documents_indexed += 1
                print(f"✓ Indexed {chunk_id}")
                
            except Exception as e:
                documents_failed += 1
                print(f"✗ Error indexing {chunk_id}: {e}")
                continue
    
    print(f"\n=== Summary ===")
    print(f"✓ Indexed: {documents_indexed}")
    print(f"✗ Failed: {documents_failed}")
    
    return documents_indexed


def handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    try:
        docs_indexed = 0
        for record in event['Records']:
            bucket = record['s3']['bucket']['name']
            key = record['s3']['object']['key']
            
            if not key.lower().endswith('.pdf'):
                print(f"Skipping non-PDF: {key}")
                continue
            
            docs_indexed += process_pdf(bucket, key)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                'documents_indexed': docs_indexed
            })
        }
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
