import tiktoken

def num_tokens_from_messages(messages, model="gpt-4o"):
    """
    Returns the number of tokens used by a list of messages.
    Adapted from OpenAI guidelines.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
    total_tokens = 0
    for message in messages:
        total_tokens += 4  # every message overhead
        for key, value in message.items():
            total_tokens += len(encoding.encode(value))
        total_tokens += 2
    return total_tokens

def normalize_weights(rating_checked, reviews_checked, price_checked):
    """
    Given truthy values for each factor (e.g., from checkboxes),
    returns normalized weights. If a factor is checked, use 1.0; if not, use 0.5.
    """
    base_rating = 1.0 if rating_checked else 0.5
    base_reviews = 1.0 if reviews_checked else 0.5
    base_price = 1.0 if price_checked else 0.5
    total = base_rating + base_reviews + base_price
    return {
        "rating": base_rating / total,
        "reviews": base_reviews / total,
        "price": base_price / total
    }
