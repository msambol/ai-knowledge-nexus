import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaPython from '@aws-cdk/aws-lambda-python-alpha';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';

export interface AiSlackBotStackProps extends cdk.StackProps {
  slackBotToken: string;
  slackSigningSecret: string;
}

export class AiSlackBotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AiSlackBotStackProps) {
    super(scope, id, props);

    // Import API info from PDF processor stack
    const queryEndpoint = ssm.StringParameter.valueFromLookup(this, '/ai-nexus/query-endpoint');
    const apiId = ssm.StringParameter.valueFromLookup(this, '/ai-nexus/api-id');
    const rootResourceId = ssm.StringParameter.valueFromLookup(this, '/ai-nexus/api-root-resource-id');

    // Import the existing API Gateway
    const api = apigateway.RestApi.fromRestApiAttributes(this, 'ImportedApi', {
      restApiId: apiId,
      rootResourceId: rootResourceId,
    });

    // ===== Slack Bot Secrets =====
    const slackBotTokenSecret = new secretsmanager.Secret(this, 'SlackBotTokenSecret', {
      secretName: 'slack-bot-token',
      secretStringValue: cdk.SecretValue.unsafePlainText(props.slackBotToken),
      description: 'Slack Bot OAuth Token',
    });
    
    const slackSigningSecret = new secretsmanager.Secret(this, 'SlackSigningSecret', {
      secretName: 'slack-signing-secret',
      secretStringValue: cdk.SecretValue.unsafePlainText(props.slackSigningSecret),
      description: 'Slack App Signing Secret',
    });

    // ===== Slack Processor (not exposed to internet) =====
    const processorLogGroup = new logs.LogGroup(this, 'ProcessorLogGroup', {
      logGroupName: '/aws/lambda/ai-knowledge-nexus-slack-processor',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const processorRole = new iam.Role(this, 'ProcessorRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const slackProcessor = new lambdaPython.PythonFunction(this, 'SlackProcessor', {
      functionName: 'ai-knowledge-nexus-slack-processor',
      description: 'Processes Slack requests (queries API, formats responses)',
      entry: 'lambdas/slack-processor',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler',
      index: 'index.py',
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      role: processorRole,
      environment: {
        SLACK_BOT_TOKEN_SECRET_ARN: slackBotTokenSecret.secretArn,
        QUERY_API_URL: queryEndpoint,
      },
      logGroup: processorLogGroup,
    });

    // Grant processor permission to call the query API with IAM auth
    slackProcessor.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['execute-api:Invoke'],
        resources: [
          `arn:aws:execute-api:${this.region}:${this.account}:${apiId}/*/POST/query`
        ]
      })
    );

    slackBotTokenSecret.grantRead(processorRole);

    // ===== Slack Webhook =====
    const webhookLogGroup = new logs.LogGroup(this, 'WebhookLogGroup', {
      logGroupName: '/aws/lambda/ai-knowledge-nexus-slack-webhook',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const webhookRole = new iam.Role(this, 'WebhookRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const slackWebhook = new lambdaPython.PythonFunction(this, 'SlackWebhook', {
      functionName: 'ai-knowledge-nexus-slack-webhook',
      description: 'Receives and verifies Slack webhooks',
      entry: 'lambdas/slack-webhook',
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler',
      index: 'index.py',
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      role: webhookRole,
      environment: {
        SLACK_SIGNING_SECRET_ARN: slackSigningSecret.secretArn,
        PROCESSOR_LAMBDA_ARN: slackProcessor.functionArn,
      },
      logGroup: webhookLogGroup,
    });

    slackProcessor.grantInvoke(webhookRole);
    slackSigningSecret.grantRead(webhookRole);

    // ===== Slack Bot API Endpoint =====
    // Note: No IAM auth - Slack needs public access, verification happens in Lambda
    const slackResource = api.root.addResource('slack');
    slackResource.addMethod('POST', new apigateway.LambdaIntegration(slackWebhook, {
      timeout: cdk.Duration.seconds(15),
    }));

    // ===== Outputs =====
    new cdk.CfnOutput(this, 'SlackBotEndpoint', {
      value: `https://${apiId}.execute-api.${this.region}.amazonaws.com/prod/slack`,
      description: 'Slack bot webhook endpoint',
    });

    new cdk.CfnOutput(this, 'WebhookLogGroupOutput', {
      value: webhookLogGroup.logGroupName,
      description: 'CloudWatch log group for Slack webhook',
    });

    new cdk.CfnOutput(this, 'ProcessorLogGroupOutput', {
      value: processorLogGroup.logGroupName,
      description: 'CloudWatch log group for Slack processor',
    });

    new cdk.CfnOutput(this, 'WebhookFunctionName', {
      value: slackWebhook.functionName,
      description: 'Slack webhook Lambda function name',
    });

    new cdk.CfnOutput(this, 'ProcessorFunctionName', {
      value: slackProcessor.functionName,
      description: 'Slack processor Lambda function name',
    });
  }
}
