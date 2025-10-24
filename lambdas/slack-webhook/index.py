import json
import os
import boto3
import hmac
import hashlib
import time
from urllib.parse import parse_qs

# Initialize clients
secrets_client = boto3.client('secretsmanager')
lambda_client = boto3.client('lambda')

# Environment variables
SLACK_SIGNING_SECRET_ARN = os.environ.get('SLACK_SIGNING_SECRET_ARN')
PROCESSOR_LAMBDA_ARN = os.environ.get('PROCESSOR_LAMBDA_ARN')


def get_slack_signing_secret():
    try:
        return secrets_client.get_secret_value(SecretId=SLACK_SIGNING_SECRET_ARN).get('SecretString')
    except Exception as e:
        print(f"Error getting Slack signing secret: {e}")
        raise


def verify_slack_request(event):
    """Verify that the request came from Slack"""
    try:
        headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
        
        slack_signature = headers.get('x-slack-signature', '')
        slack_request_timestamp = headers.get('x-slack-request-timestamp', '')
        
        if not slack_signature or not slack_request_timestamp:
            print("No signature or timestamp - allowing (likely URL verification)")
            return True
        
        try:
            timestamp = int(slack_request_timestamp)
            if abs(time.time() - timestamp) > 60 * 5:
                print("Request timestamp too old")
                return False
        except ValueError:
            print(f"Invalid timestamp: {slack_request_timestamp}")
            return False
        
        signing_secret = get_slack_signing_secret()
        sig_basestring = f"v0:{slack_request_timestamp}:{event['body']}"
        
        my_signature = 'v0=' + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(my_signature, slack_signature)
        if not is_valid:
            print(f"Signature mismatch. Expected: {my_signature}, Got: {slack_signature}")
        return is_valid
    except Exception as e:
        print(f"Error verifying Slack request: {e}")
        return False


def handler(event, context):
    print(f"Received Slack webhook request")
    
    body = event.get('body', '')
    
    # URL verification (happens during Slack app setup)
    try:
        body_json = json.loads(body)
        if body_json.get('type') == 'url_verification':
            print("Handling URL verification challenge")
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'challenge': body_json.get('challenge')})
            }
    except json.JSONDecodeError:
        pass
    
    # Verify request signature
    if not verify_slack_request(event):
        print("Request verification failed")
        return {
            'statusCode': 401,
            'body': json.dumps({'error': 'Invalid request signature'})
        }
    
    # Request is verified - parse it
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    content_type = headers.get('content-type', '')
    
    # Handle slash commands
    if 'application/x-www-form-urlencoded' in content_type.lower():
        params = parse_qs(body)
        command = params.get('command', [''])[0]
        
        if command == '/nexus':
            command_text = params.get('text', [''])[0]
            channel_id = params.get('channel_id', [''])[0]
            response_url = params.get('response_url', [''])[0]
            
            if not command_text or command_text.strip() == '':
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'response_type': 'ephemeral',
                        'text': 'Please provide a question. Usage: `/nexus What is the data retention policy?`'
                    })
                }
            
            # Invoke processor Lambda asynchronously
            try:
                payload = {
                    'type': 'slash_command',
                    'question': command_text,
                    'channel_id': channel_id,
                    'response_url': response_url
                }
                
                lambda_client.invoke(
                    FunctionName=PROCESSOR_LAMBDA_ARN,
                    InvocationType='Event',
                    Payload=json.dumps(payload)
                )
                
                print(f"Invoked processor for slash command: {command_text}")
                
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'response_type': 'in_channel',
                        'text': f'🔍 Searching Nexus: "{command_text}"'
                    })
                }
                
            except Exception as e:
                print(f"Error invoking processor: {e}")
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'response_type': 'ephemeral',
                        'text': f'Sorry, I encountered an error: {str(e)}'
                    })
                }
        else:
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'response_type': 'ephemeral',
                    'text': f'Unknown command: {command}'
                })
            }
    
    # Handle Events API (app mentions, DMs, etc.)
    try:
        body_json = json.loads(body)
        event_type = body_json.get('event', {}).get('type')
        
        # Ignore bot messages to prevent loops
        if body_json.get('event', {}).get('bot_id'):
            return {
                'statusCode': 200,
                'body': json.dumps({'ok': True})
            }
        
        if event_type == 'app_mention':
            event_data = body_json.get('event', {})
            text = event_data.get('text', '')
            channel = event_data.get('channel', '')
            thread_ts = event_data.get('thread_ts') or event_data.get('ts')
            
            import re
            text = re.sub(r'<@[A-Z0-9]+>', '', text).strip()
            
            if not text:
                # Invoke processor to send help message
                payload = {
                    'type': 'help_message',
                    'channel': channel,
                    'thread_ts': thread_ts
                }
                
                lambda_client.invoke(
                    FunctionName=PROCESSOR_LAMBDA_ARN,
                    InvocationType='Event',
                    Payload=json.dumps(payload)
                )
            else:
                # Invoke processor to handle the mention
                payload = {
                    'type': 'app_mention',
                    'question': text,
                    'channel': channel,
                    'thread_ts': thread_ts
                }
                
                lambda_client.invoke(
                    FunctionName=PROCESSOR_LAMBDA_ARN,
                    InvocationType='Event',
                    Payload=json.dumps(payload)
                )
                
                print(f"Invoked processor for mention: {text}")
        
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'ok': True})
        }
        
    except Exception as e:
        print(f"Error handling event: {e}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
