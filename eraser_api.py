import json

import requests

from config import ERASER_API_KEY

def render_diagram(provider_name: str, diagram_description: str, diagram_type: str) -> str:
    """
    Génère un diagramme via l'API Eraser.io si diagram_type ∈ {sequence, flowchart, architecture}.
    Sinon, renvoie une chaîne vide.
    """
    if not ERASER_API_KEY:
        raise ValueError("ERASER_API_KEY non configurée.")
    api_key = ERASER_API_KEY
    endpoint = "https://app.eraser.io/api/render/prompt"
    
    # Sélection du type de diagramme
    if diagram_type in ['architecture', 'archi']:
        diagram_type_str = 'cloud-architecture-diagram'
    elif diagram_type == 'flowchart':
        diagram_type_str = 'flowchart-diagram'
    elif diagram_type == 'sequence':
        diagram_type_str = 'sequence-diagram'
    else:
        return ""
    
    # Préparation des données et alignement des icones avec le provider
    diagram_description += f"prioritize icons aligned with {provider_name}. Otherwise, replace with the most representative icons."
    data = {
        "text": diagram_description,
        #"diagramType": "cloud-architecture-diagram", 
        "diagramType": diagram_type_str, 
        "mode": "standard", # "mode": "premium",
        "returnFile": False,
        "background": True,
        "theme": "light",
        "scale": "1"
    }
    
    # Appel à l'API
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        response = requests.post(endpoint, json=data, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        raise Exception(f"API Request Error: {e}") from e
    
    # Traitement de la réponse
    try:
        response_decoded = response.json()
    except json.JSONDecodeError as e:
        raise Exception(f"Erreur de décodage JSON: {str(e)}") from e
    
    # Vérifications minimales
    if 'imageUrl' not in response_decoded or 'createEraserFileUrl' not in response_decoded:
        raise Exception(f"Réponse inattendue de l'API: {response.text}")
    
    # Confection d'un JSON minimal (sous forme de chaîne)
    image_url = response_decoded['imageUrl']
    create_eraser_file_url = response_decoded['createEraserFileUrl']
    ai_render_result = (
        '{ "imageUrl": "' + image_url + '", '
        '"createEraserFileUrl": "' + create_eraser_file_url + '" }'
    )
    print("Render diagram result:", ai_render_result)
    return ai_render_result
