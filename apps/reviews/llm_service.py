import json
import requests
from django.conf import settings


LLM_SYSTEM_PROMPT = """You are a senior backend engineer conducting a code review.
Analyze the following {language} code and return ONLY a JSON object.
No explanation. No markdown. No preamble. Just raw JSON.

JSON shape:
{{
  "summary": "one sentence overall assessment",
  "bugs": [{{"line": <int>, "issue": "<string>", "severity": "high|medium|low"}}],
  "security": [{{"line": <int>, "issue": "<string>", "severity": "high|medium|low"}}],
  "quality": [{{"suggestion": "<string>", "severity": "high|medium|low"}}],
  "score": <int 0-100>
}}"""


class LLMService:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER

    def review_code(self, code: str, language: str) -> dict:
        if self.provider == 'groq':
            return self._groq_review(code, language)
        elif self.provider == 'anthropic':
            return self._anthropic_review(code, language)
        elif self.provider == 'openai':
            return self._openai_review(code, language)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def _groq_review(self, code: str, language: str) -> dict:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": settings.GROQ_MODEL or "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT.format(language=language)},
                {"role": "user", "content": f"Code to review:\n{code}"}
            ]
        }
        
        response = requests.post(url, json=data, headers=headers, timeout=60)
        
        if response.status_code != 200:
            raise Exception(f"Groq error: {response.text}")
        
        result = response.json()
        return self._parse_json_response(result['choices'][0]['message']['content'])

    def _anthropic_review(self, code: str, language: str) -> dict:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            system=LLM_SYSTEM_PROMPT.format(language=language),
            messages=[{"role": "user", "content": f"Code to review:\n{code}"}]
        )
        
        return self._parse_json_response(response.content[0].text)

    def _openai_review(self, code: str, language: str) -> dict:
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT.format(language=language)},
                {"role": "user", "content": f"Code to review:\n{code}"}
            ]
        )
        
        return self._parse_json_response(response.choices[0].message.content)

    def _parse_json_response(self, response_text: str) -> dict:
        try:
            text = response_text.strip()
            if text.startswith('```json'):
                text = text[7:]
            if text.startswith('```'):
                text = text[3:]
            if text.endswith('```'):
                text = text[:-3]
            
            data = json.loads(text.strip())
            
            if 'bugs' not in data:
                data['bugs'] = []
            if 'security' not in data:
                data['security'] = []
            if 'quality' not in data:
                data['quality'] = []
            
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response as JSON: {e}\nResponse: {response_text}")
