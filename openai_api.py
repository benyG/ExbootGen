# openai_api.py

import requests
import logging
import re
import json
import time
import random
from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_API_URL,
    OPENAI_MAX_RETRIES,
)

DOMAIN_PROMPT_TEMPLATE = (
    "Retrieve the official domains of the exam outline course for the certification "
    "{{NAME_OF_CERTIFICATION}} along with their descriptions.\n"
    "Reference Sources: only from official website of the specified certification vendor.\n"
    "If direct browsing is not available, rely on your most up-to-date knowledge of the vendor's official exam outline to provide accurate domains and descriptions, without mentioning any limitations.\n"
    "Each domain must correspond to a section from the official outline and include a concise vendor-aligned description.\n"
    "Format your response as a decodable JSON object in a single line without line breaks.\n"
    "Your answer MUST only be the requested JSON, nothing else.\n"
    "EXPECTED RESPONSE FORMAT (JSON only, no additional text):\n"
    "["
    "  {name:Domaine A,descr:Description…},"
    "  {name:Domaine B,descr:Description…} "
    "]"
)

ARTICLE_PROMPT_TEMPLATES = {
    "certification_presentation": """
Retrieve official information about exam certification: {certification} from vendor {vendor}.
Your mission is to write a SEO optimized, clear, actionable and up-to-date article, which presents the certification to the reader.
Reference Sources: only from official website of the specified certification vendor.
If direct browsing is not available, rely on your most up-to-date knowledge of the vendor's official exam outline to provide accurate informations.
RULES:
- Format your response in Markdown.
- Titles should be short.
- respect scrupulously the given Article structure only
- Formulates complete, non-robotic sentences
- Target length: 1,500–2,200 words.
- Tone: motivating, factual, without unexplained jargon.
- Zero fluff: each section must deliver useful and actionable information.
STRUCTURE:
- Certification objectives
- Targeted professions.
- Audience
- Official prerequisites.
- Exam plan: Precise format (duration, number of questions, types), language, passing score, retake policy, validity/recertification.
- How ExamBoot.net helps candidates prepare
- Study tip (≤50 words)
- A call to action with a link to start a free test: {exam_url}.
""",
    "preparation_methodology": """
Write a SEO optimized, clear, actionable and up-to-date step-by-step guide on how to Prepare for the certification exam: {certification} from {vendor}.
Include study duration, key topics, common mistakes, and preparation resources.
Integrate how ExamBoot.net features like qustions bank, AI coach, performances analysis and realistic simulations, can help accelerate learning.
Format with headings, bullet points, and a motivational tone.
Explicitly include the link to start a free ExamBoot test: {exam_url}.
RULES:
- Format your response in Markdown.
- Titles should be SEO optimized.
- Formulates complete, non-robotic sentences
- Target length: 1,500–2,200 words.
""",
    "experience_testimony": """
Write a SEO optimized, clear, actionable and up-to-date third-person or storytelling-style blog post testimony on how to pass the certification exam: {certification} from {vendor}.
Structure: presentation, motivation, challenges, strategy, results.
Make it inspiring and motivating.
Include realistic study milestones, use of ExamBoot.net, and takeaways for other candidates.
End with a call to action containing the link to start a free test: {exam_url}.
RULES:
- Format your response in Markdown.
- Titles should be SEO optimized.
- Formulates complete, non-robotic sentences
- Target length: 1,500–2,200 words.
""",
    "career_impact": """
Write a data-driven blog post on how the certification exam: {certification} from {vendor} can Boost Your career Opportunities.
Include statistics (average salaries, job titles, demand trends), examples of companies hiring certified professionals, and how ExamBoot.net helps candidates stand out.
Conclude with a call to action featuring the link to start a free ExamBoot test: {exam_url}.
RULES:
- Format your response in Markdown.
- Titles should be SEO optimized.
- Formulates complete, non-robotic sentences
- Target length: 1,500–2,200 words.
""",
    "engagement_community": """
Write an interactive blog post titled “Can You Pass This Mini {certification} from {vendor} Quiz?”
Include 5–10 sample questions with answers and explanations.
Add a section inviting readers to try the full simulation on ExamBoot.net and share their scores online.
Insert a call to action with the link to start a free ExamBoot test: {exam_url}.
RULES:
- Format your response in Markdown.
- Titles should be SEO optimized.
- Formulates complete, non-robotic sentences
- Target length: 1,500–2,200 words.
""",
}

CERTIFICATION_PRESENTATION_JSON_PROMPT = """
You are an expert certification advisor helping candidates understand {certification} from {vendor}.
Produce a JSON object describing the certification with the following exact structure:
{{
  "prerequisites": ["text1", "text2", "text3"],
  "targeted_profession": ["job title1", "job title2", "job title3"],
  "studytip": "In 20-25 words tell here how ExamBoot.net can help to prepare for the certification"
}}
Guidelines:
- Return exactly three concise bullet-style strings in both arrays, each 6-12 words.
- Mention specific skills, knowledge, or credentials relevant to {certification} in the prerequisites.
- Mention realistic job titles aligned with the certification outcome in the targeted_profession list.
- The studytip MUST be a single sentence of 20-25 words, highlight ExamBoot.net, and stay actionable.
- Respond with valid JSON only, no explanations or Markdown.
"""

TWEET_PROMPT_TEMPLATES = {
    "certification_presentation": """
Compose a short, punchy tweet introducing the certification: {certification} from {vendor}.
Highlight 1 key benefit, 1 career outcome, and mention ExamBoot.net as the platform to prepare.
Include 3 relevant hashtags and a link to the free practice test: {exam_url}.
Return only the tweet text without additional commentary.
max 280 characters.
""",
    "preparation_methodology": """
Tweet actionable exam prep tips for certification exam: {certification} from {vendor}.
Follow with 3 quick bullet points, then “💡Train smarter with ExamBoot free test: {exam_url}”.
Include 3 relevant hashtags and no additional commentary.
max 280 characters.
""",
    "experience_testimony": """
Post a motivational cote on how to pass the certification exam: {certification} from {vendor} after specified weeks of focused prep.
Try ExamBoot.net for your journey with a link to start a free test: 👉{exam_url}
Include 3 relevant hashtags.
Return only the tweet body.
max 280 characters.
""",
    "career_impact": """
Tweet key value insight from certification exam: {certification} from {vendor}.
Include 3 relevant hashtags and a link to the free practice test: {exam_url}.
Return only the tweet content.
max 280 characters.
""",
    "engagement_community": """
“Can you pass this mini {certification} from {vendor} quiz? 🤔”
Try the free practice test now on ExamBoot.net and share your score!
👉 {exam_url}
Include 3 relevant hashtags and no additional commentary.
max 280 characters.
""",
}

LINKEDIN_POST_PROMPT_TEMPLATES = {
    "certification_presentation": """
Create an engaging LinkedIn post announcing a guide about the certification: {certification} from {vendor}.
Explain why professionals should consider it, what career paths it opens, and how they can start preparing using ExamBoot.net.
End with a call to action to “Start your free practice test today: {exam_url}.”
Include 3 relevant hashtags.
Return only the LinkedIn post body without extra commentary.
""",
    "preparation_methodology": """
Write a LinkedIn post giving practical study tips for passing the certification exam: {certification} from {vendor}.
Start with a question like “Getting ready for {certification} from @{vendor}? Here’s how to study smarter.”,
give 3 concise preparation tips, and end with a link to try a free ExamBoot simulation: {exam_url}.
Include 3 relevant hashtags and no additional commentary.
""",
    "experience_testimony": """
Create a personal and authentic LinkedIn post narrating how someone succeeded in the certification exam: {certification} from {vendor}.
Use a storytelling tone, mention ExamBoot.net as part of the preparation journey, and end with encouragement for others to start.
A call to action with a link to start a free test: {exam_url}.
Include 3 relevant hashtags and return only the post text.
""",
    "career_impact": """
Draft a professional LinkedIn post highlighting the career benefits of the certification exam: {certification} from {vendor}.
Mention salary increases, job roles, and industry demand.
Use clear bullet points and finish with “Start your certification journey with ExamBoot.net.”
Include 3 relevant hashtags.
Return only the LinkedIn post body.
""",
    "engagement_community": """
Create an engaging LinkedIn post inviting readers to take a quick {certification} from {vendor} quiz.
Example intro: “Think you know [field topic]? Test yourself with our 5-question {certification} from {vendor} quiz!”
Add a link {exam_url} to the shareable test and encourage users to share their results.
Include 3 relevant hashtags and no additional commentary.
""",
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

    # Deuxième essai : détecter l'objet JSON principal lorsqu'il est entouré de texte
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


def _run_completion(prompt: str) -> str:
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    response = _post_with_retry(payload)
    resp_json = response.json()
    if "choices" not in resp_json or not resp_json["choices"]:
        raise Exception(f"Unexpected API Response: {resp_json}")

    message = resp_json["choices"][0].get("message", {})
    if "content" not in message:
        raise Exception(f"Unexpected API Response structure: {resp_json}")

    return message["content"].strip()


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
    raw_content = _run_completion(prompt)
    return clean_and_decode_json(raw_content)


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
                timeout=60,
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
            if attempt > OPENAI_MAX_RETRIES:
                raise Exception(f"API Request Error: {e}") from e

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
            if attempt > OPENAI_MAX_RETRIES:
                raise Exception(f"API Request Error: {e}") from e

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
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    response = _post_with_retry(payload)
    resp_json = response.json()
    if "choices" not in resp_json or not resp_json["choices"]:
        raise Exception(f"Unexpected API Response in generate_domains_outline: {resp_json}")

    message = resp_json["choices"][0].get("message", {})
    if "content" not in message:
        raise Exception(
            f"Unexpected API Response structure in generate_domains_outline: {resp_json}"
        )

    content = message["content"]
    return clean_and_decode_json(content)


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
        question_type_text = "Question type must be matching-question. The objective is to pair each item with its corresponding counterpart, referred to as 'Matches.'"
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
            "Questions should be of the drag and drop type. These questions can either ask for sorting (rearranging in the correct order) or categorization (sorting based on specific criteria). Some answers may simply be there to mislead the user and in this case the JSON key 'isok' has the value 0. The text of the answers must not reveal their order number in any way."
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
"""
        else:
            content_prompt = f"""
TASK: Retrieve the official course content of the domain {domain} of {certification} certification exam and generate {current} questions for that specific domain.
Main domain description: {domain_descr}
Questions: {question_type_text}
Difficulty level: {level}: {level_explained}
Practical: {practical}
{scenario_illustration_type}
Reference: Use the certification provider's official website and free exam dumps PDFs from websites like allfreedumps.com, itexams.com, exam-labs.com, pass4sure.com, etc., to find or build real exam questions.

{specific_question_quality}

Format your response as a decodable single-line JSON object.
RESPONSE FORMAT (JSON only, no additional text):
{response_format}

RULES:
1. If you want to present a line of code in your response, surround that portion with '[code]...[/code]'. This will help in formatting it.
2. If you want to present a console command or result in your response, surround that portion with '[console]...[/console]'. This will help in formatting it.
3. Strictly align questions to the content of the syllabus of the domain selected for the indicated certification.
"""

        data = {
            "model": OPENAI_MODEL,
            "messages": [
                {
                    'role': 'user',
                    'content': content_prompt
                }
            ]
        }

        try:
            response = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {OPENAI_API_KEY}'
                },
                json=data,
                timeout=60
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise Exception(f"API Request Error: {e}") from e

        resp_json = response.json()
        if 'choices' not in resp_json or not resp_json['choices']:
            raise Exception(f"Unexpected API Response: {resp_json}")
        if 'message' not in resp_json['choices'][0] or 'content' not in resp_json['choices'][0]['message']:
            raise Exception(f"Unexpected API Response structure: {resp_json}")

        content = resp_json['choices'][0]['message']['content']
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
    duration_minutes: int,
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
    duration_minutes : int
        Estimated duration in minutes for the generated lab timer.
    """

    if not OPENAI_API_KEY:
        raise Exception(
            "OPENAI_API_KEY n'est pas configurée. Veuillez renseigner la clé avant de générer un lab."
        )

    if not domains:
        raise ValueError("Au moins un domaine est requis pour générer un lab.")

    step_types_json = json.dumps(step_types, ensure_ascii=False)
    domains_label = ", ".join(domains)
    prompt = f"""Vous êtes un expert spécialisé dans la génération de lab interactifs destinée à être executé dans un outil personnalisé appellé Lab_Player.
Les labs interactifs sont basés sur des scénarios pratiques pour la préparation à des domaines précis de l'examen de certification identifiée ci-dessous.

### TASK:
Retrieve the official course content for the "{domains_label}" domains of the "{certification}" certification exam and generate a practical lab exercise with a minimum of {min_steps} steps.
Main domain description: {domain_descr}
difficulté souhaitée du lab : {difficulty}
Liste des types d'étapes attendues (tableau JSON) : {step_types_json}
certification Vendor : {provider}
Chaque lab créé est un JSON strictement valide respectant toutes les règles décrites ci-dessous, sans texte hors JSON.
Voici ci-dessous la décomposition clé par clé.

### Structure JSON attendue
## Objet racine
schema_version (string) : toujours "0.2.0".
lab (object) : contient tout le reste du scénario.
## Objet lab
- id (string, kebab-case unique) : identifiant stable du lab.
- title (string) : titre affiché.
- subtitle (string) : court complément.
- scenario_md (string Markdown) : exactement 2 ou 3 paragraphes décrivant le contexte professionnel, la mission et les objectifs liés à {provider}/{certification}.
- variables (object optionnel) : définitions de variables réutilisables. Chaque entrée suit {{ "type": "choice"|"string"|"number", ... }} et peut inclure choices, min, max, precision, etc. Référencez-les via {{variable}} dans le JSON.
- scoring (object) : {{ "max_points": <somme des points des étapes> }}.
- timer (object) : {{ "mode": "countdown", "seconds": {duration_minutes} * 60 }}. La valeur indiquée est une estimation adaptée à ce lab.
- assets (array) : liste de ressources téléchargeables ou inline.
Chaque asset est un objet avec id, kind, filename, mime, et soit inline: true + content_b64 (données encodées Base64), soit url.
- steps (array) : séquence d'étapes détaillées (minimum {min_steps} éléments) respectant les spécifications type par type.
# Gabarit JSON de référence:
{{
  "schema_version": "0.2.0",
  "lab": {{
    "id": "scenario-name",
    "title": "...",
    "subtitle": "...",
    "scenario_md": "Paragraphe 1...\n\nParagraphe 2...",
    "variables": {{
      "example_var": {{
        "type": "choice",
        "choices": ["option A", "option B"]
      }}
    }},
    "scoring": {{ "max_points": 100 }},
    "timer": {{ "mode": "countdown", "seconds": 3600 }},
    "assets": [
      {{
        "id": "file-id",
        "kind": "file",
        "filename": "policy.json",
        "mime": "application/json",
        "inline": true,
        "content_b64": "BASE64..."
      }}
    ],
    "steps": []
  }}
}}

## Structure commune de chaque étape (lab.steps[i])
{{
  "id": "unique-step-id",
  "type": "terminal | console_form | inspect_file | architecture | quiz | anticipation",
  "title": "...",
  "instructions_md": "...",
  "points": 10,
  "hints": ["Indice 1", "Indice 2"],
  "transitions": {{
    "on_success": "id-etape-suivante-ou-#end",
    "on_failure": {{ "action": "#stay" }}
  }},
  "validators": [ /* selon type */ ],
  "world_patch": [ /* optionnel, opérations JSON patch appliquées immédiatement */ ],
  "<bloc spécifique au type>": {{ ... }}
}}
- id : unique dans le lab.
- instructions_md : Markdown riche, contextualisé, rappelant l’objectif et les artefacts disponibles. When a component requires a command, instruct users here on what they should do for each component. Be cautious not to provide them with the answer.
- points : ≥ 1. La somme des points doit être égale à lab.scoring.max_points.
- hints : au moins un indice, du plus subtil au plus explicite. Possibilité d’ajouter cost par indice ({{"text":"...","cost":1}}).
- transitions.on_success : référence une autre étape ou "#end". on_failure peut garder l’utilisateur (#stay) ou pointer vers une étape de remédiation.
- validators : définissent des règles de validation strictes. Chaque validateurs peut inclure message pour un retour clair.
- world_patch : opérations appliquées avant validation. Utilisez des objets {{ "op": "set"|"unset"|"push"|"remove", "path": "...", "value": ... }}. Les chemins utilisent la notation à points (systems.firewall.enabled).

## structure du JSON par type d’étape :
#1. terminal
Bloc spécifique : propriété terminal.

"terminal": {{
  "prompt": "PS C:\\> | $ | ...",
  "environment": "bash | powershell | cloudcli | ...",
  "history": ["command already run"],
  "validators": [
    {{
      "kind": "command",
      "match": {{
        "program": "aws",
        "subcommand": ["ec2", "describe-security-groups"],
        "flags": {{
          "required": ["--group-ids"],
          "aliases": {{ "-g": "--group-ids" }}
        }},
        "args": [
          {{ "flag": "--group-ids", "expect": "sg-{{expected_group}}" }}
        ]
      }},
      "response": {{
        "stdout_template": "...",
        "stderr_template": "",
        "world_patch": [
          {{ "op": "set", "path": "systems.network.audit", "value": true }}
        ]
      }}
    }}
  ]
}}
- prompt : chaîne représentant l’invite du terminal. Doubler toutes les barres obliques inverses (\\) lorsqu’il s’agit d’environnements Windows.
- environment : identifie le shell ciblé.
- history (optionnel) : commandes déjà exécutées et visibles.
- Chaque validateur kind: "command" décrit la commande exacte attendue (programme, sous-commandes, drapeaux, arguments, options).
- La section response précise l’effet : sorties simulées (stdout_template, stderr_template) et patchs monde.
Créez autant de validateurs que nécessaires pour couvrir l’ensemble des commandes obligatoires (inclure des variantes acceptées si le scénario l’exige).
#2. console_form
Bloc spécifique : propriété form (structure UI simulée). Les validations se trouvent dans validators.
exemple:
"form": {{
  "model_path": "services.webapp.config",
  "schema": {{
    "layout": "vertical | horizontal",
    "fields": [
      {{
        "key": "mode",
        "label": "Mode",
        "widget": "toggle",
        "options": ["Off", "On"],
        "required": true
      }},
      {{
        "key": "endpoint",
        "label": "URL",
        "widget": "input",
        "placeholder": "https://api.example.com",
        "helptext": "Entrer l'URL sécurisée"
      }}
    ]
  }}
}},
"validators": [
  {{ "kind": "payload", "path": "mode", "equals": "On" }},
  {{ "kind": "world", "expect": {{ "path": "services.webapp.config.endpoint", "pattern": "^https://" }} }}
]
- model_path : emplacement dans l’état monde où stocker les valeurs soumises.
- schema.layout : "vertical" ou "horizontal".
- schema.fields[] : définir chaque champ (widget = input, textarea, select, toggle, radio, etc.), avec éventuels options, default, helptext, validation.
- Les validateurs payload inspectent directement les données soumises, tandis que world vérifie l’état monde après sauvegarde.
- Ajoutez des messages (message) et, si besoin, plusieurs vérifications combinées pour garantir que seule la bonne configuration passe.
#3. inspect_file
Bloc spécifique : clés file_ref et input.
exemple:
"file_ref": "file-id",
"input": {{
  "mode": "answer | editor",
  "prompt": "Indique la ressource mal configurée",
  "placeholder": "Ex: sg-0abc123",
  "language": "text | json | yaml | powershell | ..."
}},
"validators": [
  {{ "kind": "jsonpath_match", "path": "$.payload", "expected": "sg-0abc123" }},
  {{ "kind": "expression", "expr": "(get('payload')||'').includes('sg-0abc123')", "message": "Réponse attendue : sg-0abc123" }}
]
- file_ref : identifiant d’un asset existant.
- input.mode : "answer" (zone libre) ou "editor" (contenu éditable). Toujours préciser language pour l’éditeur (ex. json, yaml, bash).
- Les validateurs peuvent combiner jsonschema, jsonpath_match, jsonpath_absent, payload, expression, world, etc.
- S’assurer qu’une seule réponse valide passe et que les retours guident l’apprenant.
#4. architecture
Bloc spécifique : propriété architecture + validateurs stricts.
exemple:
"architecture": {{
  "mode": "freeform | slots",
  "palette_title": "Composants disponibles",
  "palette_caption": "Glisse uniquement les composants qui sont pertinents.",
  "palette": [
    {{ "id": "gw", "label": "Gateway", "icon": "🛡️", "tags": ["network"], "meta": {{"vendor": "generic"}} }},
    {{ "id": "app", "label": "App Server", "icon": "🖥️", "tags": ["compute"] }},
    {{ "id": "db", "label": "Database", "icon": "🗄️", "tags": ["storage"] }},
    {{ "id": "decoy", "label": "Legacy Fax", "icon": "📠", "tags": ["legacy"], "is_decoy": true }},
    ...
  ],
  "initial_nodes": [  ],
  "world_path": "architectures.segment",
  "help": "Double-clique sur un composant pour saisir ses commandes dans l'inspecteur.",
  "expected_world": {{
    "allow_extra_nodes": false,
    "nodes": [
      {{
        "count": 1,
        "match": {{
          "palette_id": "gw",
          "label": "Gateway-1",
          "config_contains": ["interface eth0", "policy"]
        }}
      }},
      {{
        "count": 1,
        "match": {{
          "palette_id": "app",
          "commands": ["set app-tier", "set subnet"]
        }}
      }}
    ],
    "links": [
      {{ "from": {{ "label": "Gateway-1" }}, "to": {{ "palette_id": "app" }}, "count": 1, "bidirectional": true }}
    ]
  }}
}},
"validators": [
  {{ "kind": "payload", "path": "nodes.length", "equals": 2 }},
  {{ "kind": "expression", "expr": "!(get('payload.nodes')||[]).some(n => n.palette_id === 'decoy')", "message": "Le composant leurre ne doit pas être placé." }},
  {{ "kind": "expression", "expr": "(get('payload.links')||[]).length === 1", "message": "Un seul lien est attendu." }}
]
- mode : "freeform" (mini Packet Tracer interactif) ou "slots".
- palette : Liste les composants à utiliser et rajoute un de superflu avec 'is_decoy: true', mais désigné par son nom normal. 'icon' peut être emoji, texte ou URL absolue.
- initial_nodes (optionnel) : vide car ce sera à l'utilisateur de construire l'architecture.
- L’utilisateur double-clique sur un composant pour ouvrir l’inspecteur et saisir des commandes. Le lab_player stocke commands (tableau de lignes) et/ou config (bloc texte). Les validateurs peuvent vérifier commands, config_contains, config_regex, tags, etc.
- expected_world doit rendre impossible une configuration alternative : utiliser allow_extra_nodes, nodes (avec count, match), links (définir direction, nombre, contraintes).
- Ajouter des validateurs supplémentaires pour contrôler le nombre de noeuds, l’absence du leurre, la présence de commandes, ou toute règle métier.
#5. quiz / anticipation
Bloc spécifique : clés question_md, choices, correct, explanations (optionnel).
exemple:
"question_md": "Quels contrôles implémenter pour sécuriser l'environnement ?",
"choices": [
  {{ "id": "a", "text": "Activer l'authentification multifacteur" }},
  {{ "id": "b", "text": "Laisser tous les ports ouverts" }},
  {{ "id": "c", "text": "Segmenter les workloads critiques" }}
  {{ "id": "d", "text": "Supprimer les workloads critiques" }}
],
"correct": ["a", "c"],
"explanations": {{
  "a": "Renforce le contrôle d'accès.",
  "c": "Réduit les mouvements latéraux."
}}
- choices : tableau d’objets (id, text).
- correct : tableau listant les identifiants justes (un ou plusieurs).
- explanations : optionnel, fournit un feedback ciblé par choix.
- Les validateurs peuvent inclure {{ "kind": "quiz", "expect": ["a", "c"] }} si nécessaire.
#6. anticipation
Si vous utilisez un type distinct anticipation, reprenez la même structure que quiz mais orientez les questions vers la projection ou l’analyse prospective.

### Règles supplémentaires et compatibilité
Toutes les étapes doivent rester cohérentes avec le récit de scenario_md et l’objectif pédagogique lié aux domaines sélectionné de la certification indiquée.
Chaque étape doit influencer ou vérifier l’état world de manière logique (world_patch, form.model_path, architecture.world_path, etc.).
Les indices doivent être progressifs et contextualisés.
Respecter les {step_types_json} fournis : au moins une occurrence de chaque type demandé.
Toute chaîne contenant un antislash (\\) doit être échappée en JSON (\\\\). Même règle pour les fins de ligne \n intégrées dans des chaînes.
Pas de commentaires JSON ni de virgules finales. Vérifiez que toutes les références (file_ref, transitions, palette_id, etc.) existent et que la somme des points = scoring.max_points.
Valider les dépendances entre étapes : si une étape s’appuie sur un patch monde précédent, assurez-vous que le chemin utilisé est identique.
### Format de sortie
Retourner uniquement le JSON final (formaté ou minifié), sans explication ni commentaire.
Le JSON doit être immédiatement chargeable par le Lab_Player.
"""

    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    response = _post_with_retry(payload)
    resp_json = response.json()
    if "choices" not in resp_json or not resp_json["choices"]:
        raise Exception(f"Unexpected API Response in generate_lab_blueprint: {resp_json}")
    message = resp_json["choices"][0].get("message", {})
    if "content" not in message:
        raise Exception(f"Unexpected API Response structure in generate_lab_blueprint: {resp_json}")
    content = message["content"]
    return clean_and_decode_json(content)


def analyze_certif(provider_name: str, certification: str) -> list:
    """Analyse a certification using the OpenAI API.

    The call asks the model whether the syllabus supports practical scenarios in
    various contexts (case study, architecture, configuration, console or code)
    and returns a list of dictionaries with 0/1 values.
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
[
{{"case": "0"}},
{{"archi": "0"}},
{{"config": "0"}},
{{"console": "0"}},
{{"code": "0"}}
]

Provider: {provider_name}
Certification: {certification}
"""
    data = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    response = _post_with_retry(data)
    resp_json = response.json()
    if 'choices' not in resp_json or not resp_json['choices']:
        raise Exception(f"Unexpected API Response in analyze_certif: {resp_json}")
    if 'message' not in resp_json['choices'][0] or 'content' not in resp_json['choices'][0]['message']:
        raise Exception(f"Unexpected API Response structure in analyze_certif: {resp_json}")
    
    content = resp_json['choices'][0]['message']['content']
    decoded = clean_and_decode_json(content)
    return decoded


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

        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}]
        }
        response = _post_with_retry(payload)
        resp_json = response.json()
        if 'choices' not in resp_json or not resp_json['choices']:
            raise Exception(f"Unexpected API Response: {resp_json}")
        message = resp_json['choices'][0].get('message', {})
        content = message.get('content', '')
        decoded = clean_and_decode_json(content)
        results.append(decoded)
    return results
