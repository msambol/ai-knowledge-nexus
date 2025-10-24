#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AiPdfProcessorStack } from '../lib/ai-pdf-processor-stack';
import { AiSlackBotStack } from '../lib/ai-slack-bot-stack';

const app = new cdk.App();

const openaiSettings = app.node.tryGetContext('openaiSettings') || {};
const embeddingModel = openaiSettings.embeddingModel || 'text-embedding-3-small';
const embeddingVectorLength = openaiSettings.embeddingVectorLength || '1536';
const chatModel = openaiSettings.chatModel || 'gpt-4o';

const secrets = app.node.tryGetContext('secrets') || {};
const openaiApiKey = secrets.openaiApiKey || '';
const slackBotToken = secrets.slackBotToken || '';
const slackSigningSecret = secrets.slackSigningSecret || '';

new AiPdfProcessorStack(app, 'AiPdfProcessorStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'PDF processor & API for AI Knowledge Nexus',
  tags: {
    Project: 'AiPdfProcessor',
  },
  embeddingModel,
  embeddingVectorLength,
  chatModel,
  openaiApiKey,
});

new AiSlackBotStack(app, 'AiSlackBotStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
  description: 'Slack bot integration for AI Knowledge Nexus',
  tags: {
    Project: 'AiSlackBot',
  },
  slackBotToken,
  slackSigningSecret,
});

app.synth();
