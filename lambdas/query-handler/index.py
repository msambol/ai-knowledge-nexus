import json
import boto3
import os
import re
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
OPENAI_CHAT_MODEL = os.environ.get('OPENAI_CHAT_MODEL', 'gpt-4o')
S3_BUCKET = os.environ.get('S3_BUCKET')

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


def get_embedding(text):
    """Get embedding from OpenAI"""
    try:
        client = get_openai_client()
        
        if len(text) > 30000:
            text = text[:30000]
        
        response = client.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=text
        )
        
        return response.data[0].embedding
    except Exception as e:
        print(f"Error getting embedding: {e}")
        raise


def generate_presigned_url(filename, expiration=3600):
    """Generate presigned URL for S3 object"""
    try:
        if not S3_BUCKET:
            print("S3_BUCKET not configured, skipping presigned URL")
            return None
        
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': filename
            },
            ExpiresIn=expiration
        )
        print(f"Generated presigned URL for {filename}")
        return url
    except Exception as e:
        print(f"Error generating presigned URL for {filename}: {e}")
        return None


def search_documents(query, top_k=10):
    """Pure vector search using kNN"""
    embedding = get_embedding(query)
    
    search_body = {
        "size": top_k,
        "query": {
            "knn": {
                "vector": {
                    "vector": embedding,
                    "k": top_k
                }
            }
        },
        "_source": ["text", "filename", "page", "chunk_id"]
    }
    
    response = opensearch_client.search(index=INDEX_NAME, body=search_body)
    
    results = []
    for hit in response['hits']['hits']:
        results.append({
            'text': hit['_source']['text'],
            'filename': hit['_source']['filename'],
            'page': hit['_source']['page'],
            'score': hit['_score'],
            'chunk_id': hit['_source']['chunk_id']
        })
    
    print(f"Found {len(results)} matching chunks")
    return results


def parse_sources_from_answer(answer):
    """Parse sources from OpenAI's structured response"""
    sources = []
    
    if "SOURCES:" in answer:
        parts = answer.split("SOURCES:")
        main_answer = parts[0].strip()
        sources_text = parts[1].strip() if len(parts) > 1 else ""
        
        pattern = r'-\s*([^,]+\.pdf),\s*Page\s*(\d+)'
        matches = re.findall(pattern, sources_text, re.IGNORECASE)
        
        for filename, page in matches:
            sources.append({
                'filename': filename.strip(),
                'page': int(page)
            })
        
        print(f"Parsed {len(sources)} sources from OpenAI response")
        return main_answer, sources
    else:
        print("No SOURCES section found in OpenAI response")
        return answer, []


def generate_answer(question, context_chunks):
    """Generate answer using OpenAI"""
    try:
        client = get_openai_client()
        
        context = "\n\n".join([
            f"[Source: {chunk['filename']}, Page {chunk['page']}, Relevance: {chunk['score']:.2f}]\n{chunk['text']}"
            for chunk in context_chunks
        ])
        
        messages = [
            {
                "role": "system",
                "content": """You are a helpful assistant that answers questions based on provided PDF documents. 

                            IMPORTANT INSTRUCTIONS:
                            1. Carefully read through ALL provided context chunks
                            2. Look for information that directly or indirectly answers the question
                            3. If you find relevant information, provide the answer and cite sources
                            4. Be thorough - don't give up if the answer requires piecing together multiple chunks
                            5. If the answer is truly not in the context, then say so
                            6. Pay special attention to tables, lists, and structured data in the context
                            
                            CITATION FORMAT:
                            At the end of your answer, list ONLY the sources you actually used in this exact format:
                            
                            SOURCES:
                            - filename.pdf, Page X
                            - filename.pdf, Page Y
                            
                            Only include sources you actually referenced in your answer. Do not list all provided sources."""
            },
            {
                "role": "user",
                "content": f"""Question: {question}

                    Context from PDF documents:
                    {context}

                    Please answer the question based on the context above. If you find the answer, provide it clearly and cite the specific source(s) at the end in the SOURCES format. If the information is not present in the context, state that clearly."""
            }
        ]
        
        response = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=2000
        )
        
        full_answer = response.choices[0].message.content
        print(f"Successfully generated answer using OpenAI")
        
        answer, sources = parse_sources_from_answer(full_answer)
        
        return answer, sources
        
    except Exception as e:
        print(f"Error generating answer: {e}")
        import traceback
        traceback.print_exc()
        return f"Sorry, I encountered an error generating the answer: {str(e)}", []


def handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    try:
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event
        
        question = body.get('question', '').strip()
        
        if not question:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Missing "question" field in request body'
                })
            }
        
        print(f"Processing question: {question}")
        
        search_results = search_documents(question)
        
        if not search_results:
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'answer': "I couldn't find any relevant information in the documents to answer your question.",
                    'sources': [],
                })
            }
        
        answer, sources = generate_answer(question, search_results)
        
        # If OpenAI didn't provide sources, fall back to top 3 search results
        if not sources:
            print("No sources from OpenAI, using top 3 search results as fallback")
            seen = set()
            for chunk in search_results[:3]:
                key = (chunk['filename'], chunk['page'])
                if key not in seen:
                    seen.add(key)
                    sources.append({
                        'filename': chunk['filename'],
                        'page': chunk['page']
                    })
        
        # Add presigned URLs to each source
        for source in sources:
            presigned_url = generate_presigned_url(source['filename'])
            if presigned_url:
                source['url'] = presigned_url
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'question': question,
                'answer': answer,
                'sources': sources,
            })
        }
        
    except Exception as e:
        print(f"Error in lambda_handler: {e}")
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
