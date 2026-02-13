# openai_api.py

import requests
import logging
import re
import json
import time
import random
from typing import Optional
from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_API_URL,
    OPENAI_MAX_RETRIES,
    OPENAI_TIMEOUT_SECONDS,
)

CODE_CERT_PROMPT_TEMPLATE = (
    "You are a certification researcher.\n"
    "Task: For the provider \"{provider}\", find the official exam identifier "
    "(code_cert_key) for each certification listed below.\n"
    "Rules:\n"
    "- Use ONLY the official website of the provider. Do not use third-party sites.\n"
    "- If the official identifier is not available on the official site, return an empty string.\n"
    "- Return STRICT JSON only, in a single line, with no extra commentary.\n"
    "Required JSON format:\n"
    "{{\"items\":[{{\"cert_id\":123,\"code_cert_key\":\"EXAM-123\",\"source_url\":\"https://official.example\"}}]}}\n"
    "Provide one object per input certification and preserve the cert_id.\n"
    "Input certifications (JSON array): {certifications}\n"
)

DOMAIN_PROMPT_TEMPLATE = (
    "Retrieve the official domains of the exam outline course for the certification "
    "{{NAME_OF_CERTIFICATION}} along with their descriptions.\n"
    "Reference sources: only the official website of the specified certification vendor. If the vendor's official website is unavailable, find the most reliable source to the best of your knowledge.\n"
    "If direct browsing is not available, rely on your most accurate and up-to-date knowledge of the vendor's official exam outline to provide accurate domains and descriptions, without mentioning any limitations.\n"
    "Each domain must correspond to a section from the official outline and include a concise vendor-aligned description.\n"
    "If you include sources, they must be listed only in a top-level \"sources\" array.\n"
    "Do not include URLs or citations anywhere else in the JSON.\n"
    "Format your response as a decodable JSON object in a single line without line breaks.\n"
    "Your answer MUST only be the requested JSON, nothing else.\n"
    "EXPECTED RESPONSE FORMAT (JSON only, no additional text):\n"
    "{"
    "  \"modules\": ["
    "    {\"name\": \"Domaine A\", \"descr\": \"Description…\"},"
    "    {\"name\": \"Domaine B\", \"descr\": \"Description…\"}"
    "  ],"
    "  \"sources\": [\"https://vendor.example/official-outline\"]"
    "}"
)

writing_rules = """ 
- Format the response in clean, basic HTML (h1–h3, p, ul, ol, strong).
- Provide one clear, SEO-optimized H1 title (short and impactful).
- Identify and consistently target one primary keyword and several
  semantically related keywords.
- Match the article to clear search intent (informational or educational).
- Write a compelling introduction that clearly states the problem and what the reader will learn.
- Use a logical structure with scannable H2/H3 headings.
- Target length: 1,000–1,500 words.
- Tone: factual, motivating, confident, without unexplained jargon.
- Use concrete examples, explanations, or reasoning (avoid generic advice).
- Write complete, natural, non-robotic sentences.
- End with a concise conclusion summarizing key takeaways.
- Do NOT use hashtags.
- Use emojis only if explicitly relevant and sparingly (preferably in the introduction only).
"""

writing_rules_linkedin = """ 
- First sentence must be a strong hook that challenges an assumption, highlights a surprising fact, or creates tension.
- Target audience: Professionals preparing for certifications, Students, recent graduates, HR professionals, Recruiters, Training & Development managers, Educators, trainers, Engineers, Project managers, Consultants, Technical specialists, Career advancement seekers, Learning & Development executives, Educational program directors
- Target length: 180–400 words.
- Use short paragraphs (1–3 lines) for mobile readability.
- Tone: motivating, factual, confident, without unexplained jargon.
- Use concrete examples or observations (not generic advice).
- Emojis: optional, relevant, no more than 1 per 1–2 paragraphs,
  placed only at paragraph starts or line breaks (never mid-sentence).
- Include 5–8 relevant hashtags at the end, not embedded in the text.
- always put @ before the name of the vendor to tag him.
- End with a light engagement prompt (question or reflection).
- Return only the LinkedIn post body, with no commentary or metadata.
"""

writing_rules_tweet = """ 
- Return only the tweet content without additional commentary.
- Max 280 characters.
- First line must contain a strong hook (bold claim, insight, or contrast).
- Focus on a single clear idea (no multi-topic tweets).
- Tone: concise, confident, human (no corporate or robotic phrasing).
- always put @ before the name of the vendor to tag him.
- Add relevant emojis sparingly (0–2 max), never mid-sentence.
- Include exactly 2–3 relevant hashtags at the end of the tweet.
"""

ARTICLE_PROMPT_TEMPLATES = {
    "certification_presentation": f"""
Retrieve official information about exam certification: {{certification}} from vendor {{vendor}}.
Your mission is to write a SEO optimized, clear, actionable and up-to-date article, which presents the certification to the reader.
Reference Sources: only from official website of the specified certification vendor.
If direct browsing is not available, rely on your most up-to-date knowledge of the vendor's official exam outline to provide accurate informations.
RULES:
{writing_rules}
- Respect scrupulously the given Article structure only
- Study tip must be ≤50 words
- Zero fluff: each section must deliver useful and actionable information.

STRUCTURE:
- Certification objectives
- Targeted professions.
- Audience
- Official prerequisites.
- Exam plan: Precise format (duration, number of questions, types), language, passing score, retake policy, validity/recertification.
- How ExamBoot.net helps candidates prepare
- Study tip
- A call to action with a link to start a free test:  {{exam_url}}.
""",
    "preparation_methodology": f"""
Write a SEO optimized, clear, actionable and up-to-date step-by-step guide on how to Prepare for the certification exam: {{certification}} from vendor {{vendor}}.
Include study duration, key topics, common mistakes, and preparation resources.
Integrate how ExamBoot.net features like qustions bank, AI coach, performances analysis and realistic simulations, can help accelerate learning.
Format with headings, bullet points, and a motivational tone.
Explicitly include the link to start a free ExamBoot test:  {{exam_url}}.
RULES:
{writing_rules}
""",
    "experience_testimony": f"""
Using a realistic, third-person fictional character, write a clear, actionable, storytelling-style blog post in the form of a testimonial, testifying to how the character passed with the help of ExamBoot the certification exam: {{certification}} from vendor {{vendor}}.
Structure: Context, motivation, challenges, strategy, results.
Make it inspiring and motivating.
Include realistic study milestones, use of ExamBoot.net, and takeaways for other candidates.
End with a call to action containing the link to start a free test:  {{exam_url}}.
RULES:
{writing_rules}
""",
    "career_impact": f"""
Write a data-driven blog post on how the certification exam: {{certification}} from vendor {{vendor}} can Boost Your career Opportunities.
Include statistics (average salaries, job titles, demand trends), examples of companies hiring certified professionals, and how ExamBoot.net helps candidates stand out.
Conclude with a call to action featuring the link to start a free ExamBoot test:  {{exam_url}}.
RULES:
{writing_rules}
""",
    "engagement_community": f"""
Write an interactive blog post titled “Can You Pass This Mini {{certification}} from vendor {{vendor}} Quiz?”
Include 5–10 sample questions with answers and explanations.
Add a section inviting readers to try the full simulation on ExamBoot.net and share their scores online.
Insert a call to action with the link to start a free ExamBoot test:  {{exam_url}}.
RULES:
{writing_rules}
""",
}

CERTIFICATION_PRESENTATION_JSON_PROMPT = """
You are an expert certification advisor helping candidates understand {certification} from vendor {vendor}.
Produce a JSON object describing the certification with the following exact structure:
{{
  "prerequisites": ["text1", "text2", "text3"],
  "targeted_profession": ["job title1", "job title2", "job title3"],
  "studytip": "In 50-100 words tell here how ExamBoot.net can help to prepare for the certification"
}}
Guidelines:
- Return exactly three concise bullet-style strings in both arrays, each 6-12 words.
- Mention specific skills, knowledge, or credentials relevant to {certification} in the prerequisites.
- Mention realistic job titles aligned with the certification outcome in the targeted_profession list.
- The studytip MUST be 50-100 words, highlight ExamBoot.net, and stay actionable.
- Respond with valid JSON only, no explanations or Markdown.
"""

TWEET_PROMPT_TEMPLATES = {
    "certification_presentation": f"""
Compose a short, punchy tweet introducing the certification: {{certification}} from vendor {{vendor}}.
Highlight 1 key benefit, 1 career outcome, and mention ExamBoot.net as the platform to prepare.
Include a link to the free practice test:  {{exam_url}}.
{writing_rules_tweet}
""",
    "preparation_methodology": f"""
Tweet actionable exam prep tips for certification exam: {{certification}} from vendor {{vendor}}.
Follow with 3 quick bullet points, then “💡Train smarter with ExamBoot free test:  {{exam_url}}”.
{writing_rules_tweet}
""",
    "experience_testimony": f"""
Tweet a motivational cote on how to pass the certification exam: {{certification}} from vendor {{vendor}} after specified weeks of focused prep.
Try ExamBoot.net for your journey with a link to start a free test: 👉 {{exam_url}}
{writing_rules_tweet}
""",
    "career_impact": f"""
Tweet key value insight from certification exam: {{certification}} from vendor {{vendor}}.
Include a link to the free practice test:  {{exam_url}}.
{writing_rules_tweet}
""",
    "engagement_community": f"""
Tweet an engaging challenge post inviting readers to take a quick quiz related to the certification exam: {{certification}} from vendor {{vendor}} 
Try the free practice test now on ExamBoot.net and share your score!
👉  {{exam_url}}
{writing_rules_tweet}
""",
}

LINKEDIN_POST_PROMPT_TEMPLATES = {
    "certification_presentation": f"""
Create an engaging LinkedIn post announcing a guide about the certification: {{certification}} from vendor {{vendor}}.
Explain why professionals should consider it, what career paths it opens, and how they can start preparing using ExamBoot.net.
{writing_rules_linkedin}
End with a call to action to “Start your free practice test today: {{exam_url}}.”
""",
    "preparation_methodology": f"""
Write a LinkedIn post giving practical study tips for passing the certification exam: {{certification}} from vendor {{vendor}}.
Start with a question like “Getting ready for {{certification}} from @{{vendor}}? Here’s how to study smarter.”,
give 3 concise preparation tips. 
{writing_rules_linkedin}
End with a link to try a free ExamBoot test simulation: {{exam_url}}.
""",
    "experience_testimony": f"""
Using a realistic, third-person fictional character, write a clear, actionable, storytelling-style blog post in the form of a testimonial, testifying to how the character passed with the help of ExamBoot the certification exam: {{certification}} from vendor {{vendor}}.
Make it inspiring and motivating (Context, motivation, challenges, strategy, results).
Use a storytelling tone, mention ExamBoot.net as part of the preparation journey, and end with encouragement for others to start.
{writing_rules_linkedin}
A call to action with a link to start a free test: {{exam_url}}.
""",
    "career_impact": f"""
Draft a professional LinkedIn post highlighting the career benefits of the certification exam: {{certification}} from vendor {{vendor}}.
Include statistics (average salaries, job titles, demand trends), examples of companies hiring certified professionals, and how ExamBoot.net helps candidates stand out.
Use clear bullet points and finish with “Start your certification journey with ExamBoot.net.”.
{writing_rules_linkedin}
A call to action with a link to start a free test: {{exam_url}}.
""",
    "engagement_community": f"""
Create an engaging LinkedIn post inviting readers to take a quick quiz for the certification exam: {{certification}} from vendor {{vendor}}.
Example intro: “Think you know [field topic]? Test yourself with our simulated test for {{certification}} from vendor {{vendor}}!”.
{writing_rules_linkedin}
Add a link  {{exam_url}} to the shareable test and encourage users to share their results.
""",
}

LINKEDIN_CAROUSEL_PROMPT_TEMPLATE = """
You are a LinkedIn expert (B2B, personal branding, copywriting).

Objective:
Create a 5-page LinkedIn carousel that is highly engaging, clear, and easy to read on mobile.

User input:

[QUESTION_TO_ADDRESS]

General constraints:
- Audience: professionals, decision-makers, tech/business profiles
- Tone: clear, engaging, credible, value-oriented
- Style: short sentences, strong visual impact, fast to read
- 0 to 2 emojis max per page
- Each page must encourage the reader to swipe
- Respond in English

Layout constraints (to fit a precise visual template):
- headline: 4 to 9 words, ≤ 55 characters, no lists, no line breaks.
- subtext: 1 to 2 short sentences, ≤ 120 characters, no lists, no line breaks.
- key_message: micro-CTA (3 to 6 words), ≤ 32 characters, start with an action verb.

Required structure:
You MUST respond only with valid JSON (no text before/after).

Expected JSON format:

{
  "pages": [
    {
      "page_number": 1,
      "role": "Ultra-captivating hook (question, promise, or stat)",
      "headline": "",
      "subtext": "",
      "key_message": ""
    },
    {
      "page_number": 2,
      "role": "Problem / tension + key insight",
      "headline": "",
      "subtext": "",
      "key_message": ""
    },
    {
      "page_number": 3,
      "role": "Solution / revelation",
      "headline": "",
      "subtext": "",
      "key_message": ""
    },
    {
      "page_number": 4,
      "role": "Solution / revelation (continued)",
      "headline": "",
      "subtext": "",
      "key_message": ""
    },
    {
      "page_number": 5,
      "role": "Proof / differentiation + CTA",
      "headline": "",
      "subtext": "",
      "key_message": ""
    }
  ]
}

Quality rules:
- Page 1: strong hook, stop-scrolling
- Pages 2 to 5: concrete value, zero fluff
- Each page must be actionable or thought-provoking
- JSON must be strictly valid
"""

CODE_CERT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cert_id", "code_cert_key", "source_url"],
                "properties": {
                    "cert_id": {"type": "integer"},
                    "code_cert_key": {"type": "string"},
                    "source_url": {"type": "string"},
                },
            },
        },
    },
}

DOMAIN_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["modules", "sources"],
    "properties": {
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "descr"],
                "properties": {
                    "name": {"type": "string"},
                    "descr": {"type": "string"},
                },
            },
        },
        "sources": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

CERTIFICATION_PRESENTATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prerequisites", "targeted_profession", "studytip"],
    "properties": {
        "prerequisites": {
            "type": "array",
            "items": {"type": "string"},
        },
        "targeted_profession": {
            "type": "array",
            "items": {"type": "string"},
        },
        "studytip": {"type": "string"},
    },
}

LINKEDIN_CAROUSEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pages"],
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "page_number",
                    "role",
                    "headline",
                    "subtext",
                    "key_message",
                ],
                "properties": {
                    "page_number": {"type": "integer"},
                    "role": {"type": "string"},
                    "headline": {"type": "string"},
                    "subtext": {"type": "string"},
                    "key_message": {"type": "string"},
                },
            },
        },
    },
}

QUESTIONS_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "context",
                    "scenario",
                    "diagram_type",
                    "diagram_descr",
                    "image",
                    "text",
                    "nature",
                    "level",
                    "answers",
                ],
                "properties": {
                    "context": {"type": "string"},
                    "scenario": {"type": "string"},
                    "diagram_type": {"type": "string"},
                    "diagram_descr": {"type": "string"},
                    "image": {"type": "string"},
                    "text": {"type": "string"},
                    "nature": {"type": "string"},
                    "level": {"type": "string"},
                    "answers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["value", "target", "isok"],
                            "properties": {
                                "value": {"type": "string"},
                                "target": {"type": "string"},
                                "isok": {"type": "integer"},
                            },
                        },
                    },
                },
            },
        },
    },
}

ANALYZE_CERTIF_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["case", "archi", "config", "console", "code"],
    "properties": {
        "case": {"type": "string", "enum": ["0", "1"]},
        "archi": {"type": "string", "enum": ["0", "1"]},
        "config": {"type": "string", "enum": ["0", "1"]},
        "console": {"type": "string", "enum": ["0", "1"]},
        "code": {"type": "string", "enum": ["0", "1"]},
    },
}

ASSIGN_ANSWERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question_id", "answer_ids"],
    "properties": {
        "question_id": {"type": "integer"},
        "answer_ids": {
            "type": "array",
            "items": {"type": "integer"},
        },
    },
}

COMPLETE_ANSWERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question_id", "answers"],
    "properties": {
        "question_id": {"type": "integer"},
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value", "target", "isok"],
                "properties": {
                    "value": {"type": "string"},
                    "target": {"type": "string"},
                    "isok": {"type": "integer"},
                },
            },
        },
    },
}

LAB_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "lab"],
    "properties": {
        "schema_version": {"type": "string"},
        "lab": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "title",
                "subtitle",
                "scenario_md",
                "variables",
                "scoring",
                "timer",
                "assets",
                "steps",
            ],
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "scenario_md": {"type": "string"},
                "variables": {
                    "type": "object",
                    "additionalProperties": False,
                    "patternProperties": {
                        "^.*$": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["type"],
                            "properties": {
                                "type": {"type": "string"},
                                "choices": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "min": {"type": "number"},
                                "max": {"type": "number"},
                            },
                        }
                    },
                },
                "scoring": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["max_points"],
                    "properties": {"max_points": {"type": "integer"}},
                },
                "timer": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["mode", "seconds"],
                    "properties": {
                        "mode": {"type": "string"},
                        "seconds": {"type": "integer"},
                    },
                },
                "assets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "id",
                            "kind",
                            "filename",
                            "mime",
                            "inline",
                            "content_b64",
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "kind": {"type": "string"},
                            "filename": {"type": "string"},
                            "mime": {"type": "string"},
                            "inline": {"type": "boolean"},
                            "content_b64": {"type": "string"},
                        },
                    },
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "terminal",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "terminal"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "path": {"type": "string"},
                                                "equals": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                    ]
                                                },
                                                "expect": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": ["path", "pattern"],
                                                    "properties": {
                                                        "path": {"type": "string"},
                                                        "pattern": {"type": "string"},
                                                    },
                                                },
                                                "expr": {"type": "string"},
                                                "message": {"type": "string"},
                                                "schema": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                },
                                                "match": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": [
                                                        "program",
                                                        "subcommand",
                                                        "flags",
                                                        "args",
                                                    ],
                                                    "properties": {
                                                        "program": {"type": "string"},
                                                        "subcommand": {
                                                            "type": "array",
                                                            "items": {
                                                                "type": "string"
                                                            },
                                                        },
                                                        "flags": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "required",
                                                                "aliases",
                                                            ],
                                                            "properties": {
                                                                "required": {
                                                                    "type": "array",
                                                                    "items": {
                                                                        "type": "string"
                                                                    },
                                                                },
                                                                "aliases": {
                                                                    "type": "object",
                                                                    "additionalProperties": False,
                                                                    "patternProperties": {
                                                                        "^.*$": {
                                                                            "type": "string"
                                                                        }
                                                                    },
                                                                },
                                                            },
                                                        },
                                                        "args": {
                                                            "type": "array",
                                                            "items": {
                                                                "type": "object",
                                                                "additionalProperties": False,
                                                                "required": [
                                                                    "flag",
                                                                    "expect",
                                                                ],
                                                                "properties": {
                                                                    "flag": {
                                                                        "type": "string"
                                                                    },
                                                                    "expect": {
                                                                        "type": "string"
                                                                    },
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                                "response": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": [
                                                        "stdout_template",
                                                        "stderr_template",
                                                        "world_patch",
                                                    ],
                                                    "properties": {
                                                        "stdout_template": {
                                                            "type": "string"
                                                        },
                                                        "stderr_template": {
                                                            "type": "string"
                                                        },
                                                        "world_patch": {
                                                            "type": "array",
                                                            "items": {
                                                                "type": "object",
                                                                "additionalProperties": False,
                                                                "required": [
                                                                    "op",
                                                                    "path",
                                                                    "value",
                                                                ],
                                                                "properties": {
                                                                    "op": {
                                                                        "type": "string"
                                                                    },
                                                                    "path": {
                                                                        "type": "string"
                                                                    },
                                                                    "value": {
                                                                        "type": [
                                                                            "string",
                                                                            "number",
                                                                            "boolean",
                                                                            "array",
                                                                            "null",
                                                                        ]
                                                                    },
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "terminal": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["prompt", "environment", "validators"],
                                        "properties": {
                                            "prompt": {"type": "string"},
                                            "environment": {"type": "string"},
                                            "history": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "validators": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": ["kind"],
                                                    "properties": {
                                                        "kind": {"type": "string"},
                                                        "match": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "program",
                                                                "subcommand",
                                                                "flags",
                                                                "args",
                                                            ],
                                                            "properties": {
                                                                "program": {
                                                                    "type": "string"
                                                                },
                                                                "subcommand": {
                                                                    "type": "array",
                                                                    "items": {
                                                                        "type": "string"
                                                                    },
                                                                },
                                                                "flags": {
                                                                    "type": "object",
                                                                    "additionalProperties": False,
                                                                    "required": [
                                                                        "required",
                                                                        "aliases",
                                                                    ],
                                                                    "properties": {
                                                                        "required": {
                                                                            "type": "array",
                                                                            "items": {
                                                                                "type": "string"
                                                                            },
                                                                        },
                                                                        "aliases": {
                                                                            "type": "object",
                                                                            "additionalProperties": False,
                                                                            "patternProperties": {
                                                                                "^.*$": {
                                                                                    "type": "string"
                                                                                }
                                                                            },
                                                                        },
                                                                    },
                                                                },
                                                                "args": {
                                                                    "type": "array",
                                                                    "items": {
                                                                        "type": "object",
                                                                        "additionalProperties": False,
                                                                        "required": [
                                                                            "flag",
                                                                            "expect",
                                                                        ],
                                                                        "properties": {
                                                                            "flag": {
                                                                                "type": "string"
                                                                            },
                                                                            "expect": {
                                                                                "type": "string"
                                                                            },
                                                                        },
                                                                    },
                                                                },
                                                            },
                                                        },
                                                        "response": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "stdout_template",
                                                                "stderr_template",
                                                                "world_patch",
                                                            ],
                                                            "properties": {
                                                                "stdout_template": {
                                                                    "type": "string"
                                                                },
                                                                "stderr_template": {
                                                                    "type": "string"
                                                                },
                                                                "world_patch": {
                                                                    "type": "array",
                                                                    "items": {
                                                                        "type": "object",
                                                                        "additionalProperties": False,
                                                                        "required": [
                                                                            "op",
                                                                            "path",
                                                                            "value",
                                                                        ],
                                                                        "properties": {
                                                                            "op": {
                                                                                "type": "string"
                                                                            },
                                                                            "path": {
                                                                                "type": "string"
                                                                            },
                                                                            "value": {
                                                                                "type": [
                                                                                    "string",
                                                                                    "number",
                                                                                    "boolean",
                                                                                    "array",
                                                                                    "null",
                                                                                ]
                                                                            },
                                                                        },
                                                                    },
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "form",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "console_form"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "path": {"type": "string"},
                                                "equals": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                    ]
                                                },
                                                "expect": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": ["path", "pattern"],
                                                    "properties": {
                                                        "path": {"type": "string"},
                                                        "pattern": {"type": "string"},
                                                    },
                                                },
                                                "expr": {"type": "string"},
                                                "message": {"type": "string"},
                                                "schema": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "form": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["model_path", "schema"],
                                        "properties": {
                                            "model_path": {"type": "string"},
                                            "schema": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["layout", "fields"],
                                                "properties": {
                                                    "layout": {"type": "string"},
                                                    "fields": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "key",
                                                                "label",
                                                                "widget",
                                                            ],
                                                            "properties": {
                                                                "key": {
                                                                    "type": "string"
                                                                },
                                                                "label": {
                                                                    "type": "string"
                                                                },
                                                                "widget": {
                                                                    "type": "string"
                                                                },
                                                                "options": {
                                                                    "type": "array",
                                                                    "items": {
                                                                        "type": "string"
                                                                    },
                                                                },
                                                                "required": {
                                                                    "type": "boolean"
                                                                },
                                                                "placeholder": {
                                                                    "type": "string"
                                                                },
                                                                "helptext": {
                                                                    "type": "string"
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "file_ref",
                                    "input",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "inspect_file"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "path": {"type": "string"},
                                                "equals": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                    ]
                                                },
                                                "expect": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": ["path", "pattern"],
                                                    "properties": {
                                                        "path": {"type": "string"},
                                                        "pattern": {"type": "string"},
                                                    },
                                                },
                                                "expr": {"type": "string"},
                                                "message": {"type": "string"},
                                                "schema": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "file_ref": {"type": "string"},
                                    "input": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["mode", "prompt", "placeholder", "language"],
                                        "properties": {
                                            "mode": {"type": "string"},
                                            "prompt": {"type": "string"},
                                            "placeholder": {"type": "string"},
                                            "language": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "architecture",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "architecture"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "path": {"type": "string"},
                                                "equals": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                    ]
                                                },
                                                "expect": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": ["path", "pattern"],
                                                    "properties": {
                                                        "path": {"type": "string"},
                                                        "pattern": {"type": "string"},
                                                    },
                                                },
                                                "expr": {"type": "string"},
                                                "message": {"type": "string"},
                                                "schema": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "architecture": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "mode",
                                            "palette_title",
                                            "palette_caption",
                                            "palette",
                                            "initial_nodes",
                                            "world_path",
                                            "help",
                                            "expected_world",
                                        ],
                                        "properties": {
                                            "mode": {"type": "string"},
                                            "palette_title": {"type": "string"},
                                            "palette_caption": {"type": "string"},
                                            "palette": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                    "required": [
                                                        "id",
                                                        "label",
                                                        "icon",
                                                        "tags",
                                                    ],
                                                    "properties": {
                                                        "id": {
                                                            "type": "string"
                                                        },
                                                        "label": {
                                                            "type": "string"
                                                        },
                                                        "icon": {
                                                            "type": "string"
                                                        },
                                                        "tags": {
                                                            "type": "array",
                                                            "items": {
                                                                "type": "string"
                                                            },
                                                        },
                                                        "meta": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "patternProperties": {
                                                                "^.*$": {
                                                                    "type": "string"
                                                                }
                                                            },
                                                        },
                                                        "is_decoy": {
                                                            "type": "boolean"
                                                        },
                                                    },
                                                },
                                            },
                                            "initial_nodes": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "world_path": {"type": "string"},
                                            "help": {"type": "string"},
                                            "expected_world": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": [
                                                    "allow_extra_nodes",
                                                    "nodes",
                                                    "links",
                                                ],
                                                "properties": {
                                                    "allow_extra_nodes": {
                                                        "type": "boolean"
                                                    },
                                                    "nodes": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "count",
                                                                "match",
                                                            ],
                                                            "properties": {
                                                                "count": {
                                                                    "type": "integer"
                                                                },
                                                                "match": {
                                                                    "type": "object",
                                                                    "additionalProperties": False,
                                                                    "properties": {
                                                                        "palette_id": {
                                                                            "type": "string"
                                                                        },
                                                                        "label": {
                                                                            "type": "string"
                                                                        },
                                                                        "config_contains": {
                                                                            "type": "array",
                                                                            "items": {
                                                                                "type": "string"
                                                                            },
                                                                        },
                                                                        "commands": {
                                                                            "type": "array",
                                                                            "items": {
                                                                                "type": "string"
                                                                            },
                                                                        },
                                                                    },
                                                                },
                                                            },
                                                        },
                                                    },
                                                    "links": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "additionalProperties": False,
                                                            "required": [
                                                                "from",
                                                                "to",
                                                                "count",
                                                                "bidirectional",
                                                            ],
                                                            "properties": {
                                                                "from": {
                                                                    "type": "object",
                                                                    "additionalProperties": False,
                                                                    "properties": {
                                                                        "label": {
                                                                            "type": "string"
                                                                        },
                                                                        "palette_id": {
                                                                            "type": "string"
                                                                        },
                                                                    },
                                                                },
                                                                "to": {
                                                                    "type": "object",
                                                                    "additionalProperties": False,
                                                                    "properties": {
                                                                        "label": {
                                                                            "type": "string"
                                                                        },
                                                                        "palette_id": {
                                                                            "type": "string"
                                                                        },
                                                                    },
                                                                },
                                                                "count": {
                                                                    "type": "integer"
                                                                },
                                                                "bidirectional": {
                                                                    "type": "boolean"
                                                                },
                                                            },
                                                        },
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "question_md",
                                    "choices",
                                    "correct",
                                    "explanations",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "quiz"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "expect": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "question_md": {"type": "string"},
                                    "choices": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["id", "text"],
                                            "properties": {
                                                "id": {"type": "string"},
                                                "text": {"type": "string"},
                                            },
                                        },
                                    },
                                    "correct": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "explanations": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "patternProperties": {
                                            "^.*$": {"type": "string"}
                                        },
                                    },
                                },
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                    "type",
                                    "title",
                                    "instructions_md",
                                    "points",
                                    "hints",
                                    "transitions",
                                    "validators",
                                    "world_patch",
                                    "question_md",
                                    "choices",
                                    "correct",
                                    "explanations",
                                ],
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string", "const": "anticipation"},
                                    "title": {"type": "string"},
                                    "instructions_md": {"type": "string"},
                                    "points": {"type": "integer"},
                                    "hints": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "transitions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["on_success", "on_failure"],
                                        "properties": {
                                            "on_success": {"type": "string"},
                                            "on_failure": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["action"],
                                                "properties": {
                                                    "action": {"type": "string"}
                                                },
                                            },
                                        },
                                    },
                                    "validators": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["kind"],
                                            "properties": {
                                                "kind": {"type": "string"},
                                                "expect": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                    "world_patch": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["op", "path", "value"],
                                            "properties": {
                                                "op": {"type": "string"},
                                                "path": {"type": "string"},
                                                "value": {
                                                    "type": [
                                                        "string",
                                                        "number",
                                                        "boolean",
                                                        "array",
                                                        "null",
                                                    ]
                                                },
                                            },
                                        },
                                    },
                                    "question_md": {"type": "string"},
                                    "choices": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["id", "text"],
                                            "properties": {
                                                "id": {"type": "string"},
                                                "text": {"type": "string"},
                                            },
                                        },
                                    },
                                    "correct": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "explanations": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "patternProperties": {
                                            "^.*$": {"type": "string"}
                                        },
                                    },
                                },
                            },
                        ]
                    },
                },
            },
        },
    },
}


def clean_and_decode_json(content: str) -> dict:
    """
    Nettoie le contenu (retire les balises ```json) et décode le JSON.
    En cas d'erreur, on log et on lève une exception.
    """
    logging.debug(f"Raw content received: {content}")

    # Supprimer les balises ```json et ```
    cleaned = re.sub(r'```json|```', '', content).strip()

    # Premier essai : décodage direct du JSON
    try:
        decoded_json = json.loads(cleaned)
        logging.debug(f"Decoded JSON (direct): {decoded_json}")
        return decoded_json
    except json.JSONDecodeError as direct_error:
        logging.debug(
            "Direct JSON decode failed, attempting to locate embedded object: %s",
            direct_error,
        )

    # Deuxième essai : détecter le tableau JSON principal lorsqu'il est entouré de texte
    start = cleaned.find('[')
    end = cleaned.rfind(']')
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1].strip()
        try:
            decoded_json = json.loads(snippet)
            logging.debug(f"Decoded JSON (array snippet): {decoded_json}")
            return decoded_json
        except json.JSONDecodeError as snippet_error:
            logging.debug("Array snippet decode failed: %s", snippet_error)

    # Troisième essai : détecter l'objet JSON principal lorsqu'il est entouré de texte
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1].strip()
        try:
            decoded_json = json.loads(snippet)
            logging.debug(f"Decoded JSON (snippet): {decoded_json}")
            return decoded_json
        except json.JSONDecodeError as snippet_error:
            logging.debug("Snippet decode failed: %s", snippet_error)

    logging.error("JSON Decoding Error - content was: %s", cleaned)
    err = Exception(f"JSON Decoding Error. Raw content: {cleaned}")
    setattr(err, "raw_content", cleaned)
    raise err

def _run_completion(
    prompt: str,
    *,
    model: Optional[str] = None,
    web_search_options: Optional[dict] = None,
    text_format: Optional[dict] = None,
) -> str:
    payload = _build_response_payload(prompt, model=model, text_format=text_format)
    if web_search_options is not None:
        payload["tools"] = [_build_web_search_tool(web_search_options)]
        payload["tool_choice"] = {"type": "web_search"}
        payload["include"] = ["web_search_call.action.sources"]

    response = _post_with_retry(payload)
    resp_json = response.json()
    return _extract_response_text(resp_json)


def _json_schema_format(schema: dict, name: str) -> dict:
    return {"type": "json_schema", "name": name, "strict": True, "schema": schema}


def _build_response_payload(
    prompt: str,
    *,
    model: Optional[str] = None,
    text_format: Optional[dict] = None,
) -> dict:
    payload = {
        "model": model or OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
    }
    if text_format is not None:
        payload["text"] = {"format": text_format}
    return payload


def _build_web_search_tool(options: Optional[dict]) -> dict:
    tool = {"type": "web_search"}
    if options:
        tool.update(options)
    return tool


def _extract_response_text(resp_json: dict) -> str:
    output_text = resp_json.get("output_text")
    if output_text:
        return output_text.strip()

    output = resp_json.get("output", [])
    for item in output:
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text") and content.get("text"):
                return content["text"].strip()

    raise Exception(f"Unexpected API Response structure: {resp_json}")

def _render_prompt(template_map: dict, topic_type: str, certification: str, vendor: str, exam_url: str) -> str:
    try:
        template = template_map[topic_type]
    except KeyError as exc:  # pragma: no cover - defensive programming
        raise ValueError(f"Type de sujet inconnu: {topic_type}") from exc

    return template.format(
        certification=certification,
        vendor=vendor,
        exam_url=exam_url,
    )

def generate_certification_article(
    certification: str, vendor: str, exam_url: str, topic_type: str
    ) -> str:
    """Generate the long-form certification article following the required structure."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un article."
        )

    prompt = _render_prompt(
        ARTICLE_PROMPT_TEMPLATES,
        topic_type,
        certification,
        vendor,
        exam_url,
    )
    return _run_completion(prompt)

def generate_certification_tweet(
    certification: str, vendor: str, exam_url: str, topic_type: str
    ) -> str:
    """Generate the announcement tweet for the certification launch."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un tweet."
        )

    prompt = _render_prompt(
        TWEET_PROMPT_TEMPLATES,
        topic_type,
        certification,
        vendor,
        exam_url,
    )
    return _run_completion(prompt)

def generate_certification_linkedin_post(
    certification: str, vendor: str, exam_url: str, topic_type: str
    ) -> str:
    """Generate the LinkedIn announcement post for the certification launch."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un post LinkedIn."
        )

    prompt = _render_prompt(
        LINKEDIN_POST_PROMPT_TEMPLATES,
        topic_type,
        certification,
        vendor,
        exam_url,
    )
    return _run_completion(prompt)


def generate_linkedin_carousel(subject: str, question: str) -> dict:
    """Generate the LinkedIn carousel content as structured JSON."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un carrousel LinkedIn."
        )

    subject_clean = (subject or "").strip()
    question_clean = (question or "").strip()
    if not subject_clean or not question_clean:
        raise ValueError("Le sujet et la question sont requis pour générer un carrousel.")

    user_input = f"Sujet : {subject_clean}\nQuestion : {question_clean}"
    prompt = LINKEDIN_CAROUSEL_PROMPT_TEMPLATE.replace(
        "[QUESTION_TO_ADDRESS]",
        user_input,
    )
    raw_content = _run_completion(
        prompt,
        text_format=_json_schema_format(LINKEDIN_CAROUSEL_SCHEMA, "linkedin_carousel"),
    )
    return clean_and_decode_json(raw_content)

def generate_module_blueprint_excerpt(
    certification_name: str,
    domain_name: str,
    code_cert: str | None = None,
) -> str:
    """Generate a textual blueprint excerpt for a certification domain."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un blueprint."
        )

    certification = (certification_name or "").strip()
    domain = (domain_name or "").strip()
    code_cert_clean = (code_cert or "").strip()
    if not certification or not domain:
        raise ValueError(
            "Les noms de certification et de domaine sont requis pour générer un blueprint."
        )

    code_line = f"Certification code (code_cert): {code_cert_clean}.\n" if code_cert_clean else ""
    prompt = (
        f"Using the official exam guide, produce an excerpt from the blueprint for the domain: {domain}, of the certification: {certification}.\n"
        f"{code_line}"
        "RULES:\n"
        "- 150–300 words\n"
        "- Focus only on what’s listed in the official exam blueprint and if you don’t have access to the official exam blueprint, use the most accurate and up-to-date and reliable source to the best of your knowledge.\n"
        "- If you are unsure whether a topic is covered in the certification curriculum, do not include it in the excerpt.\n"
        "- If you include sources, list them only at the end in a dedicated 'Sources:' section with bullet URLs.\n"
        "STRICT RESPONSE STRUCTURE:\n"
        "- Key focus areas from the official exam guide."
    )

    return _run_completion(
        prompt,
        web_search_options={},
    )

def _build_course_art_prompt(certification: str, vendor: str) -> str:
    """Return the course art prompt even if the template constant is missing."""

    try:
        template = COURSE_ART_PROMPT_TEMPLATE
    except NameError:  # pragma: no cover - defensive guard for partial imports
        template = """
Generate a concise JSON profile for the certification exam {certification} from vendor {vendor}.
Return **only** valid JSON following exactly this structure:
{{
  "prerequisites": ["text1", "text2", "text3"],
  "targeted_profession": ["job title1", "job title2", "job title3"],
  "studytip": "20-25 words"
}}
Rules:
- Provide a maximum of five distinct, specific prerequisite statements.
- Provide a maximum of five distinct targeted job titles that match real professional roles.
- The studytip must be up to 20-25 words that clearly states how tactically preppare for certification exam.
- Use double quotes for every string and return valid JSON without additional commentary or code fences.
"""

    return template.format(certification=certification, vendor=vendor)

def generate_certification_course_art(certification: str, vendor: str) -> dict:
    """Generate structured JSON describing the certification course."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer la fiche certification."
        )

    prompt = _build_course_art_prompt(certification, vendor)
    raw_content = _run_completion(
        prompt,
        text_format=_json_schema_format(
            CERTIFICATION_PRESENTATION_SCHEMA,
            "certification_presentation",
        ),
    )
    return clean_and_decode_json(raw_content)

def _model_temperature_override(model: str) -> Optional[float]:
    """Return the temperature override to use for the supplied model.

    Some provider models (notably ``gpt-5-mini``) reject custom temperature
    values and only accept the default setting.  Returning ``None`` ensures the
    payload omits the ``temperature`` field altogether so that the provider can
    apply its default configuration.
    """

    if model == "gpt-5-mini":
        return None

    return 0.2

def _post_with_retry(payload: dict) -> requests.Response:
    """Send a POST request to the OpenAI API with retry and backoff.

    Retries the request when the API returns HTTP 429 or any 5xx status code.
    """

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    attempt = 0
    while True:
        try:
            response = requests.post(
                OPENAI_API_URL,
                headers=headers,
                json=payload,
                timeout=OPENAI_TIMEOUT_SECONDS,
            )

            # Treat 429 and 5xx responses as retryable
            if response.status_code == 429 or 500 <= response.status_code < 600:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text}",
                    response=response,
                )

            response.raise_for_status()
            return response

        except requests.HTTPError as e:
            attempt += 1
            if attempt >= OPENAI_MAX_RETRIES:
                error_detail = ""
                if getattr(e, "response", None) is not None:
                    error_detail = e.response.text
                message = (
                    f"API Request Error after {attempt} attempts: {e}"
                )
                if error_detail:
                    message += f" | Details: {error_detail}"
                raise Exception(message) from e

            retry_after = getattr(e.response, "headers", {}).get("Retry-After")
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = None
            else:
                delay = None

            if delay is None:
                delay = min(60, (2 ** (attempt - 1)))

            delay += random.uniform(0, 1)
            logging.warning(
                f"OpenAI API call failed (attempt {attempt}/{OPENAI_MAX_RETRIES}). "
                f"Retrying in {delay:.1f}s."
            )
            time.sleep(delay)

        except requests.RequestException as e:
            attempt += 1
            if attempt >= OPENAI_MAX_RETRIES:
                raise Exception(
                    f"API Request Error after {attempt} attempts: {e}"
                ) from e

            delay = min(60, (2 ** (attempt - 1))) + random.uniform(0, 1)
            logging.warning(
                f"OpenAI API network error (attempt {attempt}/{OPENAI_MAX_RETRIES}). "
                f"Retrying in {delay:.1f}s."
            )
            time.sleep(delay)

def generate_domains_outline(certification: str) -> dict:
    """Retrieve official domains for a certification via the OpenAI API."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer des domaines."
        )
    prompt = DOMAIN_PROMPT_TEMPLATE.replace("{{NAME_OF_CERTIFICATION}}", certification)
    content = _run_completion(
        prompt,
        web_search_options={},
        text_format=_json_schema_format(DOMAIN_RESPONSE_SCHEMA, "domain_outline"),
    )
    return clean_and_decode_json(content)


def generate_code_cert_keys(provider_name: str, certifications: list[dict]) -> list[dict]:
    """Retrieve official code_cert_key identifiers for a provider's certifications."""

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer des codes."
        )

    provider = (provider_name or "").strip()
    if not provider:
        raise ValueError("Le nom du provider est requis.")
    if not certifications:
        raise ValueError("Aucune certification fournie pour la recherche.")

    payload_list = [
        {"cert_id": item.get("cert_id"), "cert_name": item.get("cert_name")}
        for item in certifications
    ]
    prompt = CODE_CERT_PROMPT_TEMPLATE.format(
        provider=provider,
        certifications=json.dumps(payload_list, ensure_ascii=False),
    )
    raw_content = _run_completion(
        prompt,
        web_search_options={},
        text_format=_json_schema_format(CODE_CERT_RESPONSE_SCHEMA, "code_cert_keys"),
    )
    decoded = clean_and_decode_json(raw_content)
    if isinstance(decoded, dict):
        items = decoded.get("items", [])
    else:
        items = decoded
    if not isinstance(items, list):
        raise ValueError("Réponse inattendue lors de la recherche des codes.")
    return items

def generate_questions(
    provider_name: str,
    certification: str,
    domain: str,
    domain_descr: str,
    level: str,
    q_type: str,
    practical: str,
    scenario_illustration_type: str,
    num_questions: int,
    batch_size: int = 5,
    use_text: bool = False
 ) -> dict:
    """
    Interroge l'API OpenAI pour générer des questions,
    puis nettoie/décode le JSON retourné via clean_and_decode_json.

    Paramètres
    ---------
    provider_name : ex. "AWS"
    certification : ex. "AWS Certified Solutions Architect"
    domain        : ex. "Security domain"
    level         : "easy", "medium", "hard"
    q_type        : ex. "qcm", "truefalse", "short-answer", "matching", "drag-n-drop"
    practical     : "no", "scenario", "scenario-illustrated"
    scenario_illustration_type : ex. "none", "archi", "console", "code", etc.
    num_questions : nombre total de questions à générer
    batch_size    : nombre de questions à générer par appel API
    """

    logging.debug(f"scenario_illustration_type: {scenario_illustration_type}")
    logging.debug(f"domain: {domain}")
    logging.debug(f"description: {domain_descr}")
    logging.debug(f"level: {level}")
    scope_phrase = "from the text provided" if use_text else "from the identified domains"
    
   # Always initialize text_for_diagram_type to ensure it's defined.
    text_for_diagram_type = ""
    if practical == "scenario-illustrated":
        text_for_diagram_type = "architecture"
        #text_for_diagram_type = "sequence|flowchart|architecture"
    
     # Construction du prompt en fonction du scenario: 'practical', 'scenario_illustration_type', ...
       
    if practical == "scenario":
        if scenario_illustration_type == 'case':
            specific_question_quality = (
                "Based on a realistic practical case scenario which must be elaborated in 2 or 3 paragraphs and integrate concepts from the identified domains. Illustrate the context of the question with an image. To do so, provides  in the 'diagram_descr' key, a detailed textual prompt to be used to generate that image, specifying components, details and any relevant annotations."
            )
        elif scenario_illustration_type == 'archi':
            specific_question_quality = (
                "Based on a realistic technical design/architecture integrating concepts from the identified domains. The question aims to assess skills in understanding, design, diagnosis and/or improvement of infrastructure and architecture."
            )
        elif scenario_illustration_type == 'config':
            specific_question_quality = (
                "Based on a described Configuration process integrating concepts from the identified domains. the question aims to assess skills in understanding, analysis, diagnosis and/or improvement of a configuration."
            )
        elif scenario_illustration_type == 'console':
            specific_question_quality = (
                "Based on a realistic console output integrating concepts from the identified domains. the question aims to assess skills in understanding, correction and/or execution of a task using command lines."
            )
        elif scenario_illustration_type == 'code':
            specific_question_quality = (
                "The question aims to assess skills in understanding, correction and/or code writing. A code example may be present or not and integrate concepts from the identified domains."
            )
        else:
            specific_question_quality = (
                "Based on a realistic context, the question must present a long practical scenario illustrated in 2 or 3 paragraphs."
            )
    elif practical == "scenario-illustrated":
        if scenario_illustration_type == 'archi':
            specific_question_quality = (
                "Based on a realistic technical design/architecture integrating concepts from the identified domains. the question aims to assess skills in understanding, design, diagnosis and/or improvement of infrastructure and architecture. Illustrate the context of the question with a diagram. To do so, provides a detailed textual description of the intended diagram in the 'diagram_descr' key, specifying components, relationships, connection links if it is a network, any relevant annotations. Set the value of 'diagram_type' json key to 'architecture'" #   The type of the diagram must be: architecture"
            )
        elif scenario_illustration_type == 'config':
            specific_question_quality = (
                "Based on a described Configuration process integrating concepts from the identified domains. the question aims to assess skills in understanding, analysis, diagnosis and/or improvement of a configuration. Illustrate the context of the question with a diagram. To do so, provides a detailed textual description of the intended diagram in the 'diagram_descr' key, specifying components, relationships, connection links if it is a network, any relevant annotations. Set the value of 'diagram_type' json key to 'architecture'" #  The type of the diagram can be: architecture"
            )
        elif scenario_illustration_type == 'console':
            specific_question_quality = (
                "Based on a realistic console output integrating concepts from the identified domains. the question aims to assess skills in understanding, correction and/or execution of a task using command lines. Illustrate the context of the question with a diagram. To do so, provides a detailed textual description of the intended diagram in the 'diagram_descr' key, specifying components, relationships, connection links if it is a network, any relevant annotations. Set the value of 'diagram_type' json key to 'architecture'" # the selected diagram type  The type of the diagram can be: architecture"
            )
        elif scenario_illustration_type == 'code':
            specific_question_quality = (
                "The question aims to assess skills in understanding, correcting and/or writing code related to the identified domains.  A code example can be presented or not to illustrate the context of the question."
            )
        else:
            specific_question_quality = (
                "Based on a realistic practical case scenario which must be elaborated in 2 or 3 paragraphs and integrate concepts from the identified domains. Illustrate the context of the question with an image. To do so, provides  in the 'diagram_descr' key, a detailed textual prompt to be used to generate that image, specifying components, details and any relevant annotations."
            )
    else:
        specific_question_quality = (
            "Not context based. The JSON keys 'context', 'diagram_type', 'diagram_descr' and 'image' must be empty as this question type does not require them."
        )
     #  Construction du prompt en fonction du Level
    if level == 'easy':
        level_explained = "Question must be based on basic syllabus concepts."
    elif level == 'medium':
        level_explained = (
            "Questions must focus on complex concepts and MUST evaluate problem solving skills. Answer choices must present tricky and challenging options."
        )
    elif level == 'hard':
        level_explained = (
            "Questions assess the skills on performance-based tasks and must focus on advanced subjects. The proposed answers must be closely designed to sow doubt and be difficult to choose."
        )
    else:
        level_explained = (
            "Questions must focus on complex concepts and MUST evaluate problem solving skills. Answer choices must present tricky and challenging options."
        )
    
    # Construction du prompt en fonction du type de question
    if q_type == 'qcm':
        question_type_text = (
            "Question type must be multi-choices. Four answer choices per question with one or two correct answers. "
            "Questions with two correct answers must end with: 'Select the TWO best answers.'"
        )
        response_format = f'''{{
    "questions": [
        {{
            "context": "[context]Put here only the Contextual, descriptive or scenario-related content of the question if applicable.[/context]",
            "scenario": "{scenario_illustration_type}",
            "diagram_type": "{text_for_diagram_type}",
            "diagram_descr": "",
            "image": "", 
            "text": "Put here only the text related to the question itself.",
            "nature": "{q_type}",
            "level": "{level}",
            "answers": [
                {{"value": "answer 1", "target": "", "isok": 0}},
                {{"value": "answer 2", "target": "", "isok": 1}},
                {{"value": "answer 3", "target": "", "isok": 0}},
                {{"value": "answer 4", "target": "", "isok": 0}}
            ]
        }}
      ]
    }}'''
    elif q_type == 'truefalse':
        question_type_text = "Question type must be true/false or Yes/No question with one correct answer."
        response_format = f'''{{
    "questions": [
        {{
            "context": "[context]Put here only the Contextual, descriptive or scenario-related content of the question if applicable otherwise, keep empty.[/context]",
            "scenario": "{scenario_illustration_type}",
            "diagram_type": "{text_for_diagram_type}",
            "diagram_descr": "",
            "image": "", 
            "text": "Put here only the text related to the question itself.",
            "nature": "{q_type}",
            "level": "{level}",
            "answers": [
                {{"value": "True", "target": "", "isok": 1}},
                {{"value": "False", "target": "", "isok": 0}}
            ]
        }}
      ]
    }}'''
    elif q_type == 'short-answer':
        question_type_text = "Question type must be short-answer. Answers should be short and concise."
        response_format = f'''{{
    "questions": [
        {{
            "context": "[context]Put here only the Contextual, descriptive or scenario-related content of the question if applicable.[/context]",
            "scenario": "{scenario_illustration_type}",
            "diagram_type": "{text_for_diagram_type}",
            "diagram_descr": "",
            "image": "", 
            "text": "Put here only the text related to the question itself. Example: Who painted the Mona Lisa?",
            "nature": "{q_type}",
            "level": "{level}",
            "answers": [
                {{"value": "Expected Answer", "target": "", "isok": 1}}
            ]
        }}
    ]
    }}'''
    elif q_type == 'matching':
        question_type_text = "Question type must be matching-question. The objective is to pair each item with its corresponding counterpart, referred to as 'Matches'. The number of possible answers can be 4, or even 5 in some cases, but must never exceed 5."
        response_format = f'''{{
    "questions": [
        {{
            "context": "[context]Put here only the Contextual, descriptive or scenario-related content of the question if applicable.[/context]",
            "scenario": "{scenario_illustration_type}",
            "diagram_type": "{text_for_diagram_type}",
            "diagram_descr": "",
            "image": "",            
            "text": "Put here only the text related to the question itself. Example: Match each country with its capital.",
            "nature": "{q_type}",
            "level": "{level}",
            "answers": [
                {{"value": "Item 1", "target": "Match 1", "isok": 1}},
                {{"value": "Item 2", "target": "Match 2", "isok": 1}},
                {{"value": "Item 3", "target": "Match 3", "isok": 1}},
                {{"value": "Item 4", "target": "Match 4", "isok": 1}}
            ]
        }}
    ]
    }}'''
    elif q_type == 'drag-n-drop':
        question_type_text = (
            "Questions should be of the drag and drop type. These questions can either ask for sorting (rearranging in the correct order) or categorization (sorting based on specific criteria). Some answers may simply be there to mislead the user and in this case the JSON key 'isok' has the value 0. The text of the answers must not reveal their order number in any way. The number of possible answers can be 4, or even 5 in some cases, but must never exceed 5."
        )
        response_format = f'''{{
  "questions": [
     {{ 
        "context": "[context]Put here only the Contextual, descriptive or scenario-related content of the question if applicable.[/context]",
        "scenario": "{scenario_illustration_type}",
        "diagram_type": "{text_for_diagram_type}",
        "diagram_descr": "",
        "image": "", 
        "text": "Put here only the text related to the question itself, not the context. Example: Drag and drop the correct image into the target zone.",
        "nature": "{q_type}",
        "level": "{level}",
        "answers": [
            {{"value": "action A", "target": "1", "isok": 1}},
            {{"value": "action B", "target": "2", "isok": 1}},
            {{"value": "action C", "target": "3", "isok": 1}},
            {{"value": "action D", "target": "", "isok": 0}}
        ]
     }}
  ]
    }}'''
    else:
        question_type_text = (
            "Question type must be multi-choices. Four answer choices per question with one or two correct answers."
            "Questions with two correct answers must end with: 'Select the TWO best answers.'"
        )
        response_format = ""
    
    specific_question_quality = specific_question_quality.replace("from the identified domains", scope_phrase)

    all_questions = []
    remaining = num_questions

    while remaining > 0:
        current = min(batch_size, remaining)

        # Construction du prompt pour ce batch
        if use_text:
            content_prompt = f"""
TASK: Use the provided text to generate {current} questions on the {domain} topic of the {certification} course.
Provided text: {domain_descr}
Questions: {question_type_text}
Difficulty level: {level}: {level_explained}
Practice: {practical}
{scenario_illustration_type}
Reference: Provided text.

{specific_question_quality}

Format your response as a decodable single-line JSON object.
RESPONSE FORMAT (JSON only, no additional text):
{response_format}

RULES:
1. If you want to present a line of code in your response, surround that portion with '[code]...[/code]'. This will help in formatting it.
2. If you want to present a console command or result in your response, surround that portion with '[console]...[/console]'. This will help in formatting it.
3. Strictly align questions to the content of the syllabus of the domain selected for the indicated certification.
4. Questions must be self-contained and cannot rely on the reader has the provided text. Do not use phrases like "in the provided text"; restate the necessary context directly in the question stem or options.
"""
        else:
            content_prompt = f"""
You are an expert exam item writer specialized in professional certification exams.

GOAL:
generate {current} questions for the specific domain titled {domain} of the following certification exam. Each question must be high-quality and exam-ready

- Certification: {certification}
- Vendor: {provider_name}
- Exam domain: {domain}
- Domain description and official objectives (blueprint excerpt):
{domain_descr}

- Questions type: {question_type_text}
- Difficulty level: {level}: {level_explained}
- Practical: {practical}
{scenario_illustration_type}

{specific_question_quality}
For each question, verify it matches real domain's objective.

Format your response as a decodable single-line JSON object.
RESPONSE FORMAT (JSON only, no additional text):
{response_format}

STRICT SCOPE:
1. Only use the information that can be logically derived from the official domain objectives and description.
2. Do NOT introduce topics, services, products, features or commands that are not clearly part of this domain for this certification.
3. If you are unsure whether a topic is in scope, consider it OUT of scope and do not create a question on it.
4. If you cannot map the question to at least one explicit objective of the domain, DO NOT include that question.
5. If the certification is identified as a technology vendor-neutral provider, the question should take this into account.

RULES:
1. If you want to present a line of code in your response, surround that portion with '[code]...[/code]'. This will help in formatting it.
2. If you want to present a console command or result in your response, surround that portion with '[console]...[/console]'. This will help in formatting it.
3. The text of each response section (value, target) must remain of a reasonable length and not exceed 150 characters.
4. Questions must be self-contained and cannot rely on the reader having seen the domain description above; avoid wording such as "according to the text" and restate any needed facts in the stem or options.
5. Ensure every correct answer can be inferred directly from the content you include in the question.
"""

        payload = _build_response_payload(
            content_prompt,
            text_format=_json_schema_format(QUESTIONS_RESPONSE_SCHEMA, "questions"),
        )

        response = _post_with_retry(payload)
        content = _extract_response_text(response.json())
        decoded = clean_and_decode_json(content)
        all_questions.extend(decoded.get("questions", []))
        remaining -= current

    return {"questions": all_questions}

def generate_lab_blueprint(
    provider: str,
    certification: str,
    domains: list[str],
    domain_descr: str,
    difficulty: str,
    min_steps: int,
    step_types: list[str],
    duration_minutes: int | None = None,
) -> dict:
    """Generate a hands-on lab scenario compatible with the Lab Player.

    Parameters
    ----------
    provider : str
        Name of the certification vendor.
    certification : str
        Certification title.
    domains : list[str]
        Ordered list containing the primary and secondary domains used for the lab.
    domain_descr : str
        Narrative description combining the selected domains.
    difficulty : str
        Requested difficulty level (easy, medium, hard).
    min_steps : int
        Minimum amount of steps the lab must contain.
    step_types : list[str]
        Allowed step types for the lab generation prompt.
    duration_minutes : int | None
        Target duration in minutes for the lab timer (optional).
    """

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un lab."
        )

    if not domains:
        raise ValueError("Au moins un domaine est requis pour générer un lab.")

    step_types_json = json.dumps(step_types, ensure_ascii=False)
    domains_label = ", ".join(domains)
    duration_clause = ""
    timer_clause = "timer: {\"mode\": \"countdown\", \"seconds\": x} — Duration must be estimated during generation."
    if duration_minutes:
        duration_clause = f"- Expected duration: {duration_minutes} minutes."
        timer_clause = (
            f"timer: {{\"mode\": \"countdown\", \"seconds\": {duration_minutes * 60}}} — "
            "Set the timer to match the expected duration."
        )
    prompt_template = """You are an expert in creating interactive labs in JSON for a tool.
    These labs simulate practical scenarios tied to specific certification exam domains.
    TASK:
    For certification exam: {certification} from vendor {vendor}.
    Retrieve the official course content for the domains "{domains_label}" and generate a practical lab with at least {min_steps} steps.
    If direct browsing is not available, rely on your most up-to-date knowledge of the vendor's official exam outline to provide accurate informations.
    - Main domain description: {domain_descr}
    - Lab Difficulty: {difficulty}
    {duration_clause}
    - Expected step types (JSON): {step_types_json}
    STRICT SCOPE:
    1. Only use the information that can be logically derived from the official domain objectives and description.
    2. Do NOT introduce topics, services, products, features or commands that are not clearly part of this domain for this certification.
    3. If you are unsure whether a topic is in scope, consider it OUT of scope and do not create a question on it.
    4. If you cannot map the question to at least one explicit objective of the domain, DO NOT include that question.
    5. If the certification is identified as a technology vendor-neutral provider, the question should take this into account.
    Each lab must be valid JSON only, following all rules below.
    The next section details its key structure.

    ### Strictly Expected JSON schema (Match the schema exactly as shown):
    ## Root Object
    schema_version: always "0.2.0".
    lab: contains all scenario data.
    ## Lab Object
    id (unique kebab-case): stable lab identifier.
    title, subtitle: main and short titles.
    scenario_md: 2–3 Markdown paragraphs describing lab scenario context, mission, and objectives related to {provider}/{certification}.
    variables (optional): reusable definitions (type: "choice"|"string"|"number", with possible choices, min, max, etc.). Use via {{variable}}.
    scoring: {"max_points": <sum of step points>}.
    {timer_clause}
    assets: array of realistic downloadable or inline resources (id, kind, filename, mime, inline:true, content_b64).
    steps: ordered list of detailed steps (≥ {min_steps}), following type-specific rules.
    # Reference JSON template:
    {
      "schema_version": "0.2.0",
      "lab": {
        "id": "scenario-name",
        "title": "...",
        "subtitle": "...",
        "scenario_md": "Paragraphs ...",
        "variables": {
          "example_var": {
            "type": "choice",
            "choices": ["option A", "option B"]
          }
        },
        "scoring": { "max_points": x },
        "timer": { "mode": "countdown", "seconds": x },
        "assets": [],
        "steps": []
      }
    }

    ## Common Asset schema (lab.asset[i])
    {
      "id": "file-id",
      "kind": "file",
      "filename": "<FILENAME>",
      "mime": "<MIME_TYPE>",
      "inline": true,
      "content_b64": "<BASE64>"
    }
    Requirements: Generate a realistic file appropriate for technical analysis for labs or CTF.
    - id: Arbitrary identifier for the asset.
    - kind: Asset type. "file" indicates a downloadable/generated file.
    - filename: Name of the generated file, including extension.
    - mime: MIME type describing the file format.
    - content_b64: Base64-encoded content of the file. Ensure Base64 decoding produces a valid file.
        
    ## Common Step schema (lab.steps[i])
    {
     "id": "unique-step-id",
     "type": "terminal | console_form | inspect_file | architecture | quiz | anticipation",
     "title": "...",
     "instructions_md": "...",
     "points": 10,
     "hints": ["Hint1", "Hint2",...],
     "transitions": {
       "on_success": "next-step-id-or-#end",
       "on_failure": { "action": "#stay" }
     },
     "validators": [ ],
     "world_patch": [ ],
     "<step-type-specific block>": {... }
    }
    id: unique per lab.
    instructions_md: Provide clear instructions for every step. When a component needs a command, explain what’s expected for each without revealing answers or decoy components. 
    points: ≥1; total equals lab.scoring.max_points.
    hints: ≥1, from subtle to explicit.
    transitions: define next step (on_success) or retry/remediation (on_failure).
    validators: define strict validation rules with optional feedback messages.
    world_patch: pre-validation JSON operations (set|unset|push|remove) using dot paths (e.g., systems.firewall.enabled).

    ## JSON schema by step type:
     #1. terminal
    Specific block: terminal property.
    "terminal": {
      "prompt": "PS C:\\> | $ | ...",
      "environment": "bash | powershell | cloudcli | ...",
      "history": [],
      "validators": [
        {
          "kind": "command",
          "match": {
            "program": "aws",
            "subcommand": ["ec2", ...],
            "flags": {
              "required": ["--group-ids"],
              "aliases": { "-g": "--group-ids" }
            },
            "args": [
              { "flag": "--group-ids", "expect": "sg-{{expected_group}}" }
            ]
          },
          "response": {
            "stdout_template": "...",
            "stderr_template": "",
            "world_patch": [
              { "op": "set", "path": "systems.network.audit", "value": true }
            ]
          }
        }
      ]
    }
    prompt: terminal prompt string; double backslashes (\\) for Windows env.
    environment: target shell.
    history (optional): previously run, visible commands.
    Each "command" validator defines the exact expected commands (program, subcommands, flags, args).
    The response sets effects (stdout_template, stderr_template, world patches).
    Add validators to cover all required or allowed command variants.
     
     #2. console_form
    Specific block: form (simulated UI). Validation goes in validators.
    "form": {
      "model_path": "services.webapp.config",
      "schema": {
        "layout": "vertical | horizontal",
        "fields": [
          {
            "key": "mode",
            "label": "Mode",
            "widget": "toggle",
            "options": ["Off", "On"],
            "required": true
          },
          {
            "key": "endpoint",
            "label": "URL",
            "widget": "input",
            "placeholder": "https://api.example.com",
            "helptext": ""Enter the secure URL"
          }
        ]
      }
    },
    "validators": [
      { "kind": "payload", "path": "mode", "equals": "On" },
      { "kind": "world", "expect": { "path": "services.webapp.config.endpoint", "pattern": "^https://" } }
    ]
    model_path: stores submitted values in world state.
    schema.layout: "vertical" or "horizontal".
    schema.fields[]: defines fields  (widget = input, textarea, select, toggle, radio, etc.) with optional options, default, helptext, validation. Default value must not be the correct expected answer.
    Payload validators check the submitted data; world validators verify the saved world state.
    Validators: "payload" checks submitted data; "world" checks saved state.
    Add messages or combined checks to ensure only the correct configurations passes.
     
     #3. inspect_file
    Specific block: file_ref and input keys.
    "file_ref": "file-id",
    "input": {
      "mode": "answer | editor",
      "prompt": "Indicate the misconfigured resource",
      "placeholder": "Ex: sg-00",
      "language": "text | json | yaml | powershell | ..."
    },
    "validators": [
    { "kind": "jsonpath_match", "path": "$.payload", "expected": "sg-0abc123" },
    { "kind": "expression", "expr": "(get('payload')||'').includes('sg-0abc123')", "message": "Expected answer: sg-0abc123" }
    ]
    file_ref: ID of an existing asset.
    input.mode: "answer" (text) or "editor" (editable); always set a language (e.g. JSON, YAML, Bash). The default must not match the expected answer.
    validators: may combine jsonschema, jsonpath_match, payload, expression, world, etc.
    Allow only one valid answer.

    #4. architecture
    Specific block: architecture property + strict validators.
    "architecture": {
    "mode": "freeform | slots",
    "palette_title": "Available Components",
    "palette_caption": "Drag components.",
    "palette": [
        { "id": "gw", "label": "Gateway", "icon": "🛡️", "tags": ["network"], "meta": {"vendor": "generic"} },
        { "id": "app", "label": "App Server", "icon": "🖥️", "tags": ["compute"] },
        { "id": "decoy", "label": "Legacy Fax", "icon": "📠", "tags": ["legacy"], "is_decoy": true },
        ...
        ],
    "initial_nodes": [  ],
    "world_path": "architectures.segment",
    "help": "Double-click a component to enter its commands.",
    "expected_world": {
    "allow_extra_nodes": false,
    "nodes": [
        {
        "count": 1,
        "match": {
        "palette_id": "gw",
        "label": "Gateway-1",
        "config_contains": ["interface eth0", "policy"]
            }
        },
        {
        "count": 1,
        "match": {
        "palette_id": "app",
        "commands": ["set app-tier", "set subnet"]
            }
        }
        ],
    "links": [
        { "from": { "label": "Gateway-1" }, "to": { "palette_id": "app" }, "count": 1, "bidirectional": true }
        ]
    }
    },
    "validators": [
        { "kind": "payload", "path": "nodes.length", "equals": 2 },
        { "kind": "expression", "expr": "!(get('payload.nodes')||[]).some(n => n.palette_id === 'decoy')", "message": "Component not needed." },
        { "kind": "expression", "expr": "(get('payload.links')||[]).length === 1", "message": "Only one link expected." }
    ]
    mode: "freeform" (interactive) or "slots".
    palette: lists all components plus one decoy (is_decoy:true) shown under its normal name; icon may be emoji, text, or URL.
    initial_nodes (optional): empty. user builds the architecture.
    Users double-click a component to enter commands or config; Validators can check commands, config_contains, config_regex, tags, etc.
    expected_world: prevent alternate setups using allow_extra_nodes, nodes (count, match), and links (direction, number, constraints).
    Add validators to enforce node count, exclude decoy, require commands, or apply business rules.

    #5. quiz / anticipation
    Specific block: keys question_md, choices, correct, explanations (optional).
    "question_md": "...",
    "choices": [
        {{ "id": "a", "text": "answer1" }},
        {{ "id": "b", "text": "answer2" }},
        {{ "id": "c", "text": "answer3" }}
        {{ "id": "d", "text": "answer4" }}
    ],
    "correct": ["a", "c"],
    "explanations": {{
    "a": "...",
    "c": "..."
    }}
    choices: array of {id, text} objects.
    correct: list of one or more correct IDs.
    explanations (optional): feedback per choice.
    validators: may include {"kind":"quiz","expect":["a","c"]}.
    #6. anticipation
    keep the quiz structure but focus questions on projection or prospective analysis.
    
    ### RULES AND COMPATIBILITY
    All steps must follow the scenario_md narrative and the learning goal for the chosen certification domains. Each step must update or check the world state (world_patch, form.model_path, architecture.world_path, etc.).
    Hints must be progressive and in context.
    Honor {step_types_json}: include each requested step type at least once.
    Escape backslashes (\\) and newlines (\\n) in JSON strings.
    No comments or trailing commas. Ensure all references (file_ref, transitions, palette_id, etc.) exist and total points = scoring.max_points.
    Keep step dependencies consistent: later steps must use the exact same paths set earlier.
    ### Output Format
    Return only the final JSON (formatted or minified), with no extra text.
    """

    prompt = (
        prompt_template.replace("{domains_label}", domains_label)
        .replace("{certification}", certification)
        .replace("{min_steps}", str(min_steps))
        .replace("{domain_descr}", domain_descr)
        .replace("{difficulty}", difficulty)
        .replace("{step_types_json}", step_types_json)
        .replace("{provider}", provider)
        .replace("{duration_clause}", duration_clause)
        .replace("{timer_clause}", timer_clause)
    )
    prompt = prompt.replace("{{", "{").replace("}}", "}")

    payload = _build_response_payload(
        prompt,
        text_format=_json_schema_format(LAB_RESPONSE_SCHEMA, "lab_blueprint"),
    )

    temperature = _model_temperature_override(OPENAI_MODEL)
    if temperature is not None:
        payload["temperature"] = temperature
    response = _post_with_retry(payload)
    content = _extract_response_text(response.json())
    return clean_and_decode_json(content)

def analyze_certif(provider_name: str, certification: str) -> dict:
    """Analyse a certification using the OpenAI API.

    The call asks the model whether the syllabus supports practical scenarios in
    various contexts (case study, architecture, configuration, console or code)
    and returns a dictionary with 0/1 values.
    """
    prompt = f"""
TASK: Retrieve the syllabus and content for the specified domain of the indicated certification exam. Analyze whether the topics typically covered for this certification support the creation of exam questions featuring practical scenarios in the following areas:
- A realistic practical case ("case") 
- The design of a technical architecture ("archi") 
- A configuration process ("config") 
- The use of console commands ("console") 
- Coding tasks ("code") 

Format your response as a decodable single-line JSON object.
For each key, set value to 1 (Yes) if the syllabus supports generating questions with a practical scenario in that area, or 0 (No) if it does not. 
Please base your analysis on the specifics of the provided syllabus. 
RESPONSE FORMAT (JSON only, no additional text):.
{{"case": "0", "archi": "0", "config": "0", "console": "0", "code": "0"}}

Provider: {provider_name}
Certification: {certification}
"""
    payload = _build_response_payload(
        prompt,
        text_format=_json_schema_format(
            ANALYZE_CERTIF_RESPONSE_SCHEMA,
            "certification_analysis",
        ),
    )
    response = _post_with_retry(payload)
    content = _extract_response_text(response.json())
    decoded = clean_and_decode_json(content)
    if isinstance(decoded, list):
        merged = {}
        for entry in decoded:
            if isinstance(entry, dict):
                merged.update(entry)
        return merged
    if isinstance(decoded, dict):
        return decoded
    return {}

def correct_questions(provider_name: str, cert_name: str, questions: list, mode: str) -> list:
    """Use OpenAI to correct or complete questions.

    Parameters
    ----------
    provider_name: str
        Name of the certification provider (e.g. "AWS").
    cert_name: str
        Name of the certification (e.g. "Solutions Architect").
    questions: list
        List of dictionaries describing questions. For ``mode=='assign'`` each
        dict must contain ``id``, ``text`` and ``answers`` (list of dicts with
        ``id`` and ``value``). For ``mode`` equal to ``'drag'`` or
        ``'matching'`` each dict needs ``id`` and ``text``.
    mode: str
        One of ``'assign'``, ``'drag'`` or ``'matching'``.

    Returns
    -------
    list
        List of decoded JSON structures returned by the model for each
        question.
    """

    results = []
    for q in questions:
        if mode == 'assign':
            answers_desc = "\n".join(
                f"{a['id']}: {a['value']}" for a in q.get('answers', [])
            )
            prompt = f"""You are validating exam questions for the {cert_name} certification from {provider_name}.
Determine which answers are correct.
Return JSON only using the following schema:
{{"question_id": <int>, "answer_ids": [<int>, ...]}}

Question ID: {q['id']}
Text: {q['text']}
Answers:\n{answers_desc}
JSON:"""
        else:
            prompt = f"""You are completing exam questions for the {cert_name} certification from {provider_name}.
Provide answer choices for this {mode} question.
Return JSON only using the following schema:
{{"question_id": <int>, "answers": [{{"value": "...", "target": "...", "isok": 1}}, ...]}}

Question ID: {q['id']}
Text: {q['text']}
JSON:"""

        schema = ASSIGN_ANSWERS_SCHEMA if mode == "assign" else COMPLETE_ANSWERS_SCHEMA
        schema_name = "assign_answers" if mode == "assign" else "complete_answers"
        payload = _build_response_payload(
            prompt,
            text_format=_json_schema_format(schema, schema_name),
        )
        response = _post_with_retry(payload)
        content = _extract_response_text(response.json())
        decoded = clean_and_decode_json(content)
        results.append(decoded)
    return results
