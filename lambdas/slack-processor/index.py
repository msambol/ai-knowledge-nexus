import json
import os
import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# Initialize clients
secrets_client = boto3.client('secretsmanager')
http = urllib3.PoolManager()

# Get AWS credentials for request signing for API GW
session = boto3.Session()
credentials = session.get_credentials()

# Environment variables
SLACK_BOT_TOKEN_SECRET_ARN = os.environ.get('SLACK_BOT_TOKEN_SECRET_ARN')
QUERY_API_URL = os.environ.get('QUERY_API_URL')


def get_slack_bot_token():
    try:
        return secrets_client.get_secret_value(SecretId=SLACK_BOT_TOKEN_SECRET_ARN).get('SecretString')
    except Exception as e:
        print(f"Error getting Slack bot token: {e}")
        raise


def post_message_to_slack(channel, text, thread_ts=None):
    """Post a message to Slack"""
    bot_token = get_slack_bot_token()
    
    payload = {
        'channel': channel,
        'text': text,
    }
    
    if thread_ts:
        payload['thread_ts'] = thread_ts
    
    headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {bot_token}'
    }
    
    try:
        response = http.request(
            'POST',
            'https://slack.com/api/chat.postMessage',
            body=json.dumps(payload).encode('utf-8'),
            headers=headers
        )
        
        result = json.loads(response.data.decode('utf-8'))
        if not result.get('ok'):
            print(f"Error posting to Slack: {result}")
        return result
    except Exception as e:
        print(f"Error posting message to Slack: {e}")
        raise


def format_sources_for_slack(sources):
    """Format sources with clickable hyperlinks for Slack - grouped by filename"""
    if not sources:
        return ""
    
    # Group sources by filename
    grouped = {}
    for source in sources:
        filename = source.get('filename', 'Unknown')
        page = source.get('page', 'N/A')
        url = source.get('url')
        
        if filename not in grouped:
            grouped[filename] = {
                'pages': [],
                'url': url
            }
        grouped[filename]['pages'].append(page)
    
    # Format output
    source_lines = []
    for filename, data in grouped.items():
        pages = data['pages']
        url = data['url']
        
        # Format pages as comma-separated list
        if len(pages) == 1:
            page_text = f"Page {pages[0]}"
        else:
            page_text = f"Pages {', '.join(map(str, pages))}"
        
        if url:
            # Escape pipe and angle brackets that break Slack's link format
            safe_url = url.replace('|', '%7C').replace('<', '%3C').replace('>', '%3E')
            source_lines.append(f"• <{safe_url}|{filename} ({page_text})>")
        else:
            source_lines.append(f"• {filename} ({page_text})")
    
    return "\n".join(source_lines)


def query_pdf_api(question):
    """Query the PDF Nexus API with IAM authentication"""
    try:
        payload = {'question': question}
        body = json.dumps(payload)
        
        print(f"Querying API: {QUERY_API_URL}")
        
        # Create AWS request for signing
        request = AWSRequest(
            method='POST',
            url=QUERY_API_URL,
            data=body,
            headers={
                'Content-Type': 'application/json',
            }
        )
        
        # Sign the request with SigV4
        region = os.environ.get('AWS_REGION', 'us-east-1')
        SigV4Auth(credentials, 'execute-api', region).add_auth(request)
        
        # Make the signed request
        response = http.request(
            'POST',
            QUERY_API_URL,
            body=body,
            headers=dict(request.headers),
            timeout=30.0
        )
        
        print(f"API Response status: {response.status}")
        response_text = response.data.decode('utf-8')
        
        if response.status == 403:
            print("Access denied - IAM authorization failed")
            return {
                'answer': "Sorry, I don't have permission to access the knowledge base (IAM auth failed).",
                'sources': []
            }
        
        if response.status != 200:
            print(f"API error response: {response_text}")
            return {
                'answer': f"Sorry, the API returned an error (status {response.status}).",
                'sources': []
            }
        
        if not response_text or response_text.strip() == '':
            return {
                'answer': "Sorry, the API returned an empty response.",
                'sources': []
            }
        
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            return {
                'answer': "Sorry, I received an invalid response from the knowledge base.",
                'sources': []
            }
        
        return result
        
    except Exception as e:
        print(f"Error querying PDF API: {e}")
        import traceback
        traceback.print_exc()
        return {
            'answer': f"Sorry, I encountered an error: {str(e)}",
            'sources': []
        }


def handler(event, context):
    print(f"Processing Slack request: {event.get('type')}")
    
    request_type = event.get('type')
    
    # Handle slash command
    if request_type == 'slash_command':
        question = event.get('question', '')
        response_url = event.get('response_url', '')
        
        try:
            # Query the knowledge base
            result = query_pdf_api(question)
            answer = result.get('answer', 'No answer found.')
            sources = result.get('sources', [])
            
            # Format response
            response_text = f"*❓ Question*\n{question}\n\n"
            response_text += f"*💡 Answer*\n{answer}\n\n"
            
            if sources:
                response_text += f"*📚 Sources*\n{format_sources_for_slack(sources)}"
            
            # Send response back to Slack via response_url
            payload = {
                'response_type': 'in_channel',
                'replace_original': True,
                'text': response_text
            }
            
            http.request(
                'POST',
                response_url,
                body=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                timeout=30.0
            )
            
            print("Successfully sent response to Slack")
            return {'statusCode': 200, 'body': 'Success'}
            
        except Exception as e:
            print(f"Error processing slash command: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                error_payload = {
                    'response_type': 'in_channel',
                    'replace_original': True,
                    'text': f'Sorry, I encountered an error: {str(e)}'
                }
                http.request(
                    'POST',
                    response_url,
                    body=json.dumps(error_payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
            except:
                pass
            
            return {'statusCode': 500, 'body': str(e)}
    
    # Handle app mention
    elif request_type == 'app_mention':
        question = event.get('question', '')
        channel = event.get('channel', '')
        thread_ts = event.get('thread_ts', '')
        
        try:
            # Post acknowledgment
            post_message_to_slack(channel, f'🔍 Searching Nexus: "{question}"', thread_ts)
            
            # Query the knowledge base
            result = query_pdf_api(question)
            answer = result.get('answer', 'No answer found.')
            sources = result.get('sources', [])
            
            # Format response
            response_text = f"*❓ Question*\n{question}\n\n"
            response_text += f"*💡 Answer*\n{answer}\n\n"
            
            if sources:
                response_text += f"*📚 Sources*\n{format_sources_for_slack(sources)}"
            
            # Post answer
            post_message_to_slack(channel, response_text, thread_ts)
            
            print("Successfully handled app mention")
            return {'statusCode': 200, 'body': 'Success'}
            
        except Exception as e:
            print(f"Error processing app mention: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                post_message_to_slack(channel, f'Sorry, I encountered an error: {str(e)}', thread_ts)
            except:
                pass
            
            return {'statusCode': 500, 'body': str(e)}
    
    # Handle help message
    elif request_type == 'help_message':
        channel = event.get('channel', '')
        thread_ts = event.get('thread_ts', '')
        
        try:
            post_message_to_slack(
                channel,
                "Please ask me a question! For example: What is the data retention policy?",
                thread_ts
            )
            return {'statusCode': 200, 'body': 'Success'}
        except Exception as e:
            print(f"Error sending help message: {e}")
            return {'statusCode': 500, 'body': str(e)}
    
    else:
        print(f"Unknown request type: {request_type}")
        return {'statusCode': 400, 'body': 'Unknown request type'}
