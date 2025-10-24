#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "\n${BLUE}=== Getting Stack Outputs ===${NC}"

# Get stack outputs
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name AiPdfProcessorStack \
  --query 'Stacks[0].Outputs[?OutputKey==`PdfBucketName`].OutputValue' \
  --output text)

API_URL=$(aws cloudformation describe-stacks \
  --stack-name AiPdfProcessorStack \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

OPENSEARCH_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name AiPdfProcessorStack \
  --query 'Stacks[0].Outputs[?OutputKey==`OpenSearchEndpoint`].OutputValue' \
  --output text)

PDF_PROCESSOR_LOG=$(aws cloudformation describe-stacks \
  --stack-name AiPdfProcessorStack \
  --query 'Stacks[0].Outputs[?OutputKey==`PdfProcessorLogGroupOutput`].OutputValue' \
  --output text)

QUERY_HANDLER_LOG=$(aws cloudformation describe-stacks \
  --stack-name AiPdfProcessorStack \
  --query 'Stacks[0].Outputs[?OutputKey==`QueryHandlerLogGroupOutput`].OutputValue' \
  --output text)

echo -e "${GREEN}Bucket Name:${NC} $BUCKET_NAME"
echo -e "${GREEN}API URL:${NC} $API_URL"
echo -e "${GREEN}OpenSearch Endpoint:${NC} $OPENSEARCH_ENDPOINT"
echo ""


# List documents (using IAM auth)
echo -e "${BLUE}=== List Documents ===${NC}"
awscurl --service execute-api "${API_URL}documents" | jq
echo ""

# Query
echo -e "${BLUE}=== Query #1 ===${NC}"
echo "Question: What is the data retention period for board meeting minutes?"
awscurl --service execute-api -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the minimum data retention period for storing board meeting minutes?"}' \
  "${API_URL}query" | jq
echo ""

# Query again
echo -e "${BLUE}=== Query #2 ===${NC}"
echo "Question: Can you describe our levels of data classification?"
awscurl --service execute-api -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "Can you describe our levels of data classification?"}' \
  "${API_URL}query" | jq
echo ""

# Query again
echo -e "${BLUE}=== Query #3 ===${NC}"
echo "Question: Can you summarize our AI policy?"
awscurl --service execute-api -X POST \
  -H "Content-Type: application/json" \
  -d '{"question": "Can you summarize our AI policy?"}' \
  "${API_URL}query" | jq
echo ""

echo -e "${GREEN}=== Query Testing Complete ===${NC}"
