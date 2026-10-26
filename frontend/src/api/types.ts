// Типы публичного API. Схемы сгенерированы из docs/openapi.json (`npm run gen:api`);
// поле `insight` в InsightOut — свободный объект, поэтому его форма описана здесь по contracts/insight.schema.json.
import type { components } from './schema';

type S = components['schemas'];

export type Role = S['Role'];
export type Me = S['MeOut'];
export type CompanyBrief = S['CompanyBrief'];
export type FeedSummary = S['FeedSummaryOut'];
export type CompanySummary = S['CompanySummary'];
export type InsightCard = S['InsightCard'];
export type InsightState = S['InsightState'];
export type Feed = S['FeedOut'];
export type InsightOut = S['InsightOut'];
export type Trace = S['TraceOut'];
export type Profile = S['ProfileOut'];
export type ProfileSection = S['ProfileSection'];
export type ProfileUpdate = S['ProfileUpdateIn'];
export type Graph = S['GraphOut'];
export type GraphEdge = S['GraphEdge'];
export type Notification = S['NotificationOut'];
export type Notifications = S['NotificationsOut'];
export type CrmRequest = S['RequestOut'];
export type SourceClick = S['SourceClickOut'];
export type FeedbackIn = S['FeedbackIn'];
export type ActionIn = S['ActionIn'];
export type StateAction = S['StateIn']['action'];
export type UiEventType = S['UiEventIn']['type'];
export type QuestionIntent = S['InsightQuestionIn']['intent'];
export type ManualNewsItem = S['ManualNewsItem'];
export type ManualNewsOut = S['ManualNewsOut'];
export type Digest = S['DigestOut'];
export type Market = S['MarketOut'];
export type MarketIndicator = S['MarketIndicatorOut'];
export type NewsSubmitIn = S['NewsSubmitIn'];
export type NewsSubmitOut = S['NewsSubmitOut'];
export type AdminAnalyticsDocument = S['AdminAnalyticsDocumentOut'];
export type AnalyticsUploadOut = S['AnalyticsUploadOut'];
export type GenerationOverview = S['GenerationOverviewOut'];
export type GenerationRetryOut = S['GenerationRetryOut'];
export type Alert = S['AlertOut'];
export type LlmProviderKey = S['LlmProviderKeyOut'];
export type Discover = S['DiscoverOut'];
export type FeedCandidate = S['FeedCandidate'];

export type FeedFilter = 'all' | 'urgent' | 'risks' | 'opportunities' | 'saved' | 'hidden';
export type Polarity = 'risk' | 'opportunity' | 'mixed' | 'neutral';
export type ImpactLevel = 'low' | 'medium' | 'high' | 'critical';
export type StatementKind = 'fact' | 'bank_analytics' | 'model_inference' | 'hypothesis';

export interface Source {
  id: string;
  type: 'news' | 'bank_analytics' | 'company_data' | 'user_data' | 'market_data' | string;
  ref_id: string | null;
  url: string | null;
  title: string;
  publisher: string | null;
  published_at: string | null;
  page: number | null;
}

export interface Statement {
  id: string;
  text: string;
  kind: StatementKind;
  confidence: number;
  source_refs: string[];
  feature_refs?: string[];
}

export interface Disclaimer {
  code: string;
  text: string;
}

export interface InsightBody {
  schema_version: string;
  insight_id: string | null;
  company_id: string;
  event_id: string;
  role_variant: Role;
  trace_id: string;
  title: string;
  summary: string;
  polarity: Polarity;
  urgent: boolean;
  event: {
    title: string;
    summary: string;
    event_type: string;
    occurred_at: string;
    participants: { name: string; type: string; company_id: string | null }[];
    key_facts: { text: string; source_refs: string[] }[];
  };
  why_it_matters: { text: string; feature_refs: string[]; statement_refs: string[] }[];
  consequences: {
    risks: Consequence[];
    opportunities: Consequence[];
    scenarios: {
      name: string;
      logic: string;
      plausibility: 'low' | 'medium' | 'high';
      signals: string[];
      statement_refs: string[];
    }[];
  };
  impact: {
    level: ImpactLevel;
    score: number;
    scale_version: number;
    rationale: string;
    quantitative: {
      metric: string;
      value: number;
      unit: string;
      basis: string;
      period: string;
      feature_refs: string[];
    }[];
  };
  actions: {
    type: 'contact_manager' | 'callback' | 'application' | 'analyze' | 'monitor' | 'read_source' | string;
    text: string;
    product_id: string | null;
    source_ref: string | null;
  }[];
  bank_view: {
    coverage: 'direct' | 'related' | 'none' | string;
    positions: {
      text: string;
      quote: string;
      analytics_chunk_id: string;
      document_id: string;
      page: number;
      source_ref: string;
    }[];
    model_disagrees_with_bank: boolean;
    disagreement_note: string | null;
  };
  statements: Statement[];
  relevance: {
    score: number;
    breakdown: Record<string, number>;
    matched_features: string[];
    scorer: string;
  };
  sources: Source[];
  disclaimers: Disclaimer[];
  models_used: { name: string; purpose: string }[];
  generated_at: string;
  expires_at: string;
}

export interface Consequence {
  text: string;
  horizon: string;
  drivers: string[];
  statement_refs: string[];
}

export interface BankProduct {
  product_id: string;
  name: string;
  description: string;
  tags: string[];
  url: string | null;
  source: string;
}

export interface AnalyticsDocument {
  document_id: string;
  title: string;
  file_name: string;
  doc_date: string | null;
  topics: string[];
  pages: number;
  chunks: number;
  extraction: string;
  ocr_confidence: number | null;
  origin: string;
}

// Сообщение из истории вопросов (`/insights/{id}/questions`, `/ask/history`).
export interface QaMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  intent: string | null;
  status: string;
  run_id: string | null;
  sources: Source[];
  statements: Statement[];
  created_at: string;
}

// Итог ответа агента после проверки бэкендом (событие `done`).
export interface ChatFinal {
  answer: string;
  sources: Source[];
  statements: Statement[];
  disclaimers: Disclaimer[];
  limitations?: string[];
  [key: string]: unknown;
}
