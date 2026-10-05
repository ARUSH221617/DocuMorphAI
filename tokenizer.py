"""
DocuMorph AI - Tiktoken Tokenizer & Pre-Flight Budgeting Estimator
Provides:
1. Exact per-request token estimation using tiktoken (cl100k_base & o200k_base).
2. Multimodal image / page visual token weighting (~800-1600 tokens/page).
3. Pre-flight context window utilization and cost budgeting.
"""

from typing import Dict, Any, Optional
import tiktoken
import db

# Cache encoders in memory
_ENCODERS: Dict[str, Any] = {}

def get_encoder(model_name: str = ""):
    """Retrieve optimal tiktoken encoding for model."""
    m = (model_name or "").lower()
    enc_name = "o200k_base" if ("gpt-4o" in m or "o1" in m or "o3" in m) else "cl100k_base"
    if enc_name not in _ENCODERS:
        try:
            _ENCODERS[enc_name] = tiktoken.get_encoding(enc_name)
        except Exception:
            _ENCODERS[enc_name] = tiktoken.get_encoding("cl100k_base")
    return _ENCODERS[enc_name]

def count_text_tokens(text: str, model_name: str = "") -> int:
    """Count exact tokens for a text string using tiktoken."""
    if not text:
        return 0
    try:
        enc = get_encoder(model_name)
        return len(enc.encode(text))
    except Exception:
        # Fallback 4 chars per token approximation
        return max(1, len(text) // 4)

def estimate_image_tokens(page_count: int = 1, high_res: bool = True) -> int:
    """
    Estimate multimodal tokens for rendered document pages.
    OpenAI/Gemini Vision standard: ~800 to 1600 tokens per high-res page image.
    """
    if not page_count or page_count <= 0:
        return 0
    per_page = 1200 if high_res else 600
    return page_count * per_page

def estimate_preflight_budget(
    text: str,
    model_name: str = "",
    page_count: int = 0,
    expected_output_tokens: Optional[int] = None
) -> Dict[str, Any]:
    """
    Calculate comprehensive pre-flight budget for an upcoming request:
    - Text tokens
    - Image vision tokens
    - Expected completion tokens
    - Context window ceiling (loaded from API)
    - Context window % utilization
    - Estimated USD cost
    """
    text_tokens = count_text_tokens(text, model_name)
    image_tokens = estimate_image_tokens(page_count, high_res=True)
    prompt_tokens = text_tokens + image_tokens

    # Default expected output: ~1.2x prompt text or minimum 1000 tokens
    est_completion = expected_output_tokens if expected_output_tokens is not None else max(800, int(text_tokens * 1.2))
    total_est_tokens = prompt_tokens + est_completion

    ctx_info = db.calculate_context_usage(model_name, prompt_tokens, est_completion)
    est_cost = db.calculate_cost(model_name, prompt_tokens, est_completion)

    return {
        "model": model_name,
        "text_tokens": text_tokens,
        "image_tokens": image_tokens,
        "prompt_tokens": prompt_tokens,
        "estimated_completion_tokens": est_completion,
        "total_estimated_tokens": total_est_tokens,
        "context_limit": ctx_info["context_limit"],
        "context_percent": ctx_info["context_percent"],
        "formatted_percent": ctx_info["formatted_percent"],
        "formatted_summary": ctx_info["formatted_summary"],
        "estimated_cost_usd": est_cost,
        "formatted_cost": db.format_cost(est_cost)
    }
