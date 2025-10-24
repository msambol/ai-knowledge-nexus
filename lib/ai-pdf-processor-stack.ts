import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaPython from '@aws-cdk/aws-lambda-python-alpha';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as opensearch from 'aws-cdk-lib/aws-opensearchserverless';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';

export interface AiPdfProcessorStackProps extends cdk.StackProps {
  embeddingModel: string;
  embeddingVectorLength: string;
  chatModel: string;
  openaiApiKey: string;
}
export class AiPdfProcessorStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AiPdfProcessorStackProps) {
    super(scope, id, props);

    // ===== S3 Bucket for PDFs =====
    const pdfBucket = new s3.Bucket(this, 'DocBucket', {
      bucketName: `ai-knowledge-nexus-${this.account}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ===== OpenAI API Key Secret =====
    const openaiSecret = new secretsmanager.Secret(this, 'OpenAISecret', {
      secretName: 'openai-api-key',
      secretStringValue: cdk.SecretValue.unsafePlainText(props.openaiApiKey),
      description: 'OpenAI API Key for embeddings and chat',
    });

    // ===== OpenSearch Serverless Collection =====
    
    // Encryption policy
    const encryptionPolicy = new opensearch.CfnSecurityPolicy(this, 'EncryptionPolicy', {
      name: 'pdf-vectors-encryption',
      type: 'encryption',
      policy: JSON.stringify({
        Rules: [
          {
            ResourceType: 'collection',
            Resource: ['collection/pdf-vectors'],
          },
        ],
        AWSOwnedKey: true,
      }),
    });

    // Network policy
    const networkPolicy = new opensearch.CfnSecurityPolicy(this, 'NetworkPolicy', {
      name: 'pdf-vectors-network',
      type: 'network',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'collection',
              Resource: ['collection/pdf-vectors'],
            },
          ],
          AllowFromPublic: true,
        },
      ]),
    });

    // Create the collection
    const collection = new opensearch.CfnCollection(this, 'VectorCollection', {
      name: 'pdf-vectors',
      type: 'VECTORSEARCH',
      description: 'Vector store for PDF knowledge nexus',
    });
    collection.addDependency(encryptionPolicy);
    collection.addDependency(networkPolicy);

    // Data access policy
    const dataAccessPolicy = new opensearch.CfnAccessPolicy(this, 'DataAccessPolicy', {
      name: 'pdf-vectors-access',
      type: 'data',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'collection',
              Resource: ['collection/pdf-vectors'],
              Permission: [
                'aoss:CreateCollectionItems',
                'aoss:DeleteCollectionItems',
                'aoss:UpdateCollectionItems',
                'aoss:DescribeCollectionItems',
              ],
            },
            {
              ResourceType: 'index',
              Resource: ['index/pdf-vectors/*'],
              Permission: [
                'aoss:CreateIndex',
                'aoss:DeleteIndex',
                'aoss:UpdateIndex',
                'aoss:DescribeIndex',
                'aoss:ReadDocument',
                'aoss:WriteDocument',
              ],
            },
          ],
          Principal: [`arn:aws:iam::${this.account}:root`],
        },
      ]),
    });

    // ===== IAM Role for Lambda Functions =====
    const lambdaRole = new iam.Role(this, 'LambdaExecutionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Add OpenSearch Serverless permissions
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['aoss:APIAccessAll', 'aoss:*'],
        resources: [collection.attrArn],
      })
    );

    pdfBucket.grantRead(lambdaRole);
    openaiSecret.grantRead(lambdaRole);

    const commonOpenSearchEnvironment = {
      OPENSEARCH_ENDPOINT: collection.attrCollectionEndpoint,
      OPENSEARCH_COLLECTION_ARN: collection.attrArn,
      OPENSEARCH_INDEX_NAME: 'nexus',
    }

    const commonOpenAiEnvironment = {
      OPENAI_SECRET_ARN: openaiSecret.secretArn,
      OPENAI_EMBEDDING_MODEL: props.embeddingModel || 'text-embedding-3-small',
      OPENAI_EMBEDDING_VECTOR_LENGTH: props.embeddingVectorLength || '1536',
      OPENAI_CHAT_MODEL: props.chatModel || 'gpt-4o',
    };

    // ===== PDF Processor Lambda =====
    const pdfProcessorLogGroup = new logs.LogGroup(this, 'PdfProcessorLogGroup', {
      logGroupName: '/aws/lambda/ai-knowledge-nexus-pdf-processor',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const pdfProcessor = new lambdaPython.PythonFunction(this, 'PdfProcessor', {
      functionName: 'ai-knowledge-nexus-pdf-processor',
      description: 'Extracts content from PDFs and loads into OpenSearch',
      entry: 'lambdas/pdf-processor',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler',
      index: 'index.py',
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      role: lambdaRole,
      environment: {
        ...commonOpenSearchEnvironment, 
        ...commonOpenAiEnvironment
      },
      logGroup: pdfProcessorLogGroup,
    });

    // Add S3 trigger for PDF uploads
    pdfBucket.addEventNotification(s3.EventType.OBJECT_CREATED, new s3n.LambdaDestination(pdfProcessor), {
      suffix: '.pdf',
    });

    // ===== Query Handler Lambda =====
    const queryHandlerLogGroup = new logs.LogGroup(this, 'QueryHandlerLogGroup', {
      logGroupName: '/aws/lambda/ai-knowledge-nexus-query-handler',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const queryHandler = new lambdaPython.PythonFunction(this, 'QueryHandler', {
      functionName: 'ai-knowledge-nexus-query-handler',
      description: 'Handles queries from API GW',
      entry: 'lambdas/query-handler',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler',
      index: 'index.py',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      role: lambdaRole,
      environment: {
        S3_BUCKET: pdfBucket.bucketName,
        ...commonOpenSearchEnvironment, 
        ...commonOpenAiEnvironment
      },
      logGroup: queryHandlerLogGroup,
    });
    pdfBucket.grantRead(queryHandler);

    // ===== List Documents Handler Lambda =====
    const listDocumentsLogGroup = new logs.LogGroup(this, 'ListDocumentsLogGroup', {
      logGroupName: '/aws/lambda/ai-knowledge-nexus-list-documents',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const listDocumentsHandler = new lambdaPython.PythonFunction(this, 'ListDocumentsHandler', {
      functionName: 'ai-knowledge-nexus-list-documents',
      description: 'List documents in OpenSearch',
      entry: 'lambdas/list-documents',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler',
      index: 'index.py',
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: lambdaRole,
      environment: commonOpenSearchEnvironment,
      logGroup: listDocumentsLogGroup,
    });

    // ===== API Gateway =====

    // ===== API Gateway CloudWatch Logs Role =====
    const apiGatewayCloudWatchRole = new iam.Role(this, 'ApiGatewayCloudWatchRole', {
      assumedBy: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonAPIGatewayPushToCloudWatchLogs'),
      ],
    });

    // Set the CloudWatch role for API Gateway account settings
    new apigateway.CfnAccount(this, 'ApiGatewayAccount', {
      cloudWatchRoleArn: apiGatewayCloudWatchRole.roleArn,
    });

    const api = new apigateway.RestApi(this, 'PdfQueryApi', {
      restApiName: 'Knowledge Nexus API',
      description: 'API for querying PDF knowledge nexus',
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
      },
    });

    // Query endpoint: POST /query (IAM protected)
    const queryResource = api.root.addResource('query');
    queryResource.addMethod('POST', new apigateway.LambdaIntegration(queryHandler, {
      requestTemplates: {
        'application/json': '{ "statusCode": "200" }',
      }}), {
      authorizationType: apigateway.AuthorizationType.IAM,
    });

    // Documents list endpoint: GET /documents (IAM protected)
    const documentsResource = api.root.addResource('documents');
    documentsResource.addMethod('GET', new apigateway.LambdaIntegration(listDocumentsHandler), {
      authorizationType: apigateway.AuthorizationType.IAM,
    });

    // Save API info in SSM so we can retrieve it in the Slack stack
    new ssm.StringParameter(this, 'ApiUrlParam', {
      parameterName: '/ai-nexus/api-url',
      stringValue: api.url,
      description: 'API Gateway URL',
    });

    new ssm.StringParameter(this, 'ApiIdParam', {
      parameterName: '/ai-nexus/api-id',
      stringValue: api.restApiId,
      description: 'API Gateway REST API ID',
    });

    new ssm.StringParameter(this, 'ApiRootResourceIdParam', {
      parameterName: '/ai-nexus/api-root-resource-id',
      stringValue: api.root.resourceId,
      description: 'API Gateway root resource ID',
    });

    new ssm.StringParameter(this, 'QueryEndpointParam', {
      parameterName: '/ai-nexus/query-endpoint',
      stringValue: `${api.url}query`,
      description: 'Full query endpoint URL',
    });

    // ===== Outputs =====
    new cdk.CfnOutput(this, 'PdfBucketName', {
      value: pdfBucket.bucketName,
      description: 'S3 bucket for PDFs',
      exportName: 'PdfBucketName',
    });

    new cdk.CfnOutput(this, 'OpenSearchEndpoint', {
      value: collection.attrCollectionEndpoint,
      description: 'OpenSearch Serverless endpoint',
      exportName: 'OpenSearchEndpoint',
    });

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway URL',
      exportName: 'ApiUrl',
    });

    new cdk.CfnOutput(this, 'QueryEndpoint', {
      value: `${api.url}query`,
      description: 'Query endpoint (IAM protected) - POST with {"question": "your question"}',
      exportName: 'QueryEndpoint',
    });

    new cdk.CfnOutput(this, 'DocumentsEndpoint', {
      value: `${api.url}documents`,
      description: 'List all indexed documents (IAM protected)',
      exportName: 'DocumentsEndpoint',
    });

    new cdk.CfnOutput(this, 'PdfProcessorLogGroupOutput', {
      value: pdfProcessorLogGroup.logGroupName,
      description: 'CloudWatch log group for PDF processor',
    });

    new cdk.CfnOutput(this, 'QueryHandlerLogGroupOutput', {
      value: queryHandlerLogGroup.logGroupName,
      description: 'CloudWatch log group for query handler',
    });

    new cdk.CfnOutput(this, 'OpenAISecretArn', {
      value: openaiSecret.secretArn,
      description: 'ARN of OpenAI API Key secret',
    });

    new cdk.CfnOutput(this, 'SlackBotEndpoint', {
      value: `${api.url}slack`,
      description: 'Slack bot webhook endpoint - configure in Slack Bot Stack',
      exportName: 'SlackBotEndpoint',
    });
  }
}
