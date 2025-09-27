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
    "Format your response as a decodable JSON object in a single line without line breaks.\n"
    "Your answer MUST only be the requested JSON, nothing else.\n"
    "EXPECTED RESPONSE FORMAT (JSON only, no additional text):\n"
    "{ \n"
    "  modules: [\n"
    "       {\n"
    "        module_name: Domain Name A,\n"
    "        module_descr: Domain Name A description\n"
    "       {\n"
    "       module_name: Domain Name B,\n"
    "       module_descr: Domain Name B description\n"
    "      }\n"
    "  ]\n"
    "}"
)

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
