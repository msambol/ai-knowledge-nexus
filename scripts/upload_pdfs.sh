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

# List documents (using AWS SigV4 signing)
echo -e "${BLUE}=== List Documents ===${NC}"
awscurl --service execute-api "${API_URL}documents" | jq
echo ""

# Upload PDFs
echo -e "${BLUE}=== Upload PDFs ===${NC}"
echo "Uploading docs to S3..."
aws s3 cp "${PROJECT_ROOT}/docs/ai_usage_policy.pdf" "s3://${BUCKET_NAME}/ai_usage_policy.pdf"
aws s3 cp "${PROJECT_ROOT}/docs/data_retention_policy.pdf" "s3://${BUCKET_NAME}/data_retention_policy.pdf"

if [ $? -eq 0 ]; then
  echo -e "${GREEN}✓ PDFs uploaded successfully${NC}"
else
  echo -e "${RED}✗ Failed to upload PDFs${NC}"
  exit 1
fi

echo ""
echo -e "${BLUE}=== Waiting for processing (30 seconds) ===${NC}"
echo "You can watch the logs in another terminal with:"
echo "  aws logs tail $PDF_PROCESSOR_LOG --follow"
echo ""

for i in {30..1}; do
  printf "\rWaiting... %02d seconds remaining" $i
  sleep 1
done
echo ""
echo ""

# List documents again (using AWS SigV4 signing)
echo -e "${BLUE}=== List Documents ===${NC}"
awscurl --service execute-api "${API_URL}documents" | jq
echo ""

echo -e "${GREEN}=== PDF Upload Complete ===${NC}"
