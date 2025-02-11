import time
import logging
import json
import math
import os
import openai
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

openai.api_key = os.getenv("OPENAI_API_KEY_PROBOOK")
openai_client = openai.Client(api_key=openai.api_key)

class AmazonShoppingAssistant:
    def __init__(self):
        self.driver = self.setup_driver()

    def setup_driver(self):
        """
        Sets up Selenium WebDriver with Chrome.
        (Uncomment the headless option for production use.)
        """
        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # Uncomment for headless mode.
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.93 Safari/537.36"
        )
        driver = webdriver.Chrome(options=chrome_options)
        driver.implicitly_wait(10)  # seconds
        return driver

    def parse_query(self, query):
        """
        Fallback parser – simply returns a default structure.
        """
        return {"item": query, "max_price": None, "min_rating": None, "prime": False}

    def parse_query_with_openai(self, query):
        """
        Uses the OpenAI API to extract structured search criteria from a natural language query.
        Expected output is a JSON object with keys:
          - item (string)
          - max_price (number or null)
          - min_rating (number or null)
          - prime (boolean)
        """
        prompt = (
            "Extract the following search criteria from the user's shopping query:\n"
            "1. item: the product to search for (string).\n"
            "2. max_price: the maximum price (number) if specified, else null.\n"
            "3. min_rating: the minimum rating (number) if indicated (for example, if the query says 'good reviews', assume 4.0), else null.\n"
            "4. prime: true if Prime shipping is required, false otherwise.\n"
            "Return the result as a valid JSON object with keys 'item', 'max_price', 'min_rating', and 'prime'.\n"
            f"User query: {query}"
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You extract structured search criteria from natural language shopping queries."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )
            answer = response.choices[0].message.content.strip()
            filters = json.loads(answer)
            logging.info("Parsed filters using OpenAI: %s", filters)
            return filters
        except Exception as e:
            logging.error("OpenAI API error in parse_query_with_openai: %s. Using fallback.", e)
            return self.parse_query(query)

    def parse_product_details_with_openai(self, html):
        """
        Uses the OpenAI API to extract product details from the HTML snippet of an Amazon product listing.
        Expected output is a JSON object with keys:
          - title (string)
          - price (number or null)
          - rating (number or null)
          - reviews (integer or null)
          - prime (boolean)
          - url (string or null)
        """
        prompt = (
            "Extract the following details from the provided HTML snippet of an Amazon product listing:\n"
            "1. title: the product's title (string).\n"
            "2. price: the product's price (number) if available, otherwise null.\n"
            "3. rating: the product's rating (number) if available, otherwise null.\n"
            "4. reviews: the number of reviews (integer) if available, otherwise null.\n"
            "5. prime: true if the product has Prime shipping, false otherwise.\n"
            "6. url: the product's URL (string) if available, otherwise null.\n"
            "Return a JSON object with keys 'title', 'price', 'rating', 'reviews', 'prime', and 'url'.\n"
            f"HTML snippet: {html}"
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You extract product details from an Amazon product listing HTML snippet."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            answer = response.choices[0].message.content.strip()
            product_details = json.loads(answer)
            return product_details
        except Exception as e:
            logging.error("OpenAI API error in parse_product_details_with_openai: %s", e)
            return {"title": "No title", "price": None, "rating": None, "reviews": None, "prime": False, "url": None}

    def perform_search(self, filters):
        """
        Navigates to Amazon, enters the search term, and applies filters.
        (Price filter is applied via URL parameters if max_price is set.)
        """
        amazon_url = "https://www.amazon.com/"
        logging.info("Navigating to Amazon homepage")
        self.driver.get(amazon_url)
        time.sleep(2)  # Pause to mimic human browsing

        try:
            search_box = self.driver.find_element(By.ID, "twotabsearchtextbox")
            search_box.clear()
            search_box.send_keys(filters["item"])
            search_box.submit()
            logging.info("Submitted search query: %s", filters["item"])
            time.sleep(3)
        except Exception as e:
            logging.error("Error during search submission: %s", e)
            return

        if filters.get("max_price"):
            current_url = self.driver.current_url
            new_url = f"{current_url}&low-price=0&high-price={filters['max_price']}"
            logging.info("Applying price filter via URL: %s", new_url)
            self.driver.get(new_url)
            time.sleep(3)

    def extract_products(self):
        """
        Finds product elements on the search results page and uses OpenAI to parse the entire HTML
        snippet for each product to extract its details. Also stores the Selenium element for later use.
        """
        products = []
        try:
            product_elements = self.driver.find_elements(By.XPATH, "//div[contains(@data-cel-widget, 'search_result')]")
            logging.info("Found %d product elements", len(product_elements))
        except Exception as e:
            logging.error("Error finding product elements: %s", e)
            return products

        # Process a subset of product elements (for example, the first 10).
        for elem in product_elements[:10]:
            html = elem.get_attribute("outerHTML")
            product_details = self.parse_product_details_with_openai(html)
            # Store the Selenium element for later clicking.
            product_details["element"] = elem
            products.append(product_details)
        return products

    def get_user_priority_weights(self):
        """
        Asks the user what factors they care about.
        If the user types 'default', the agent will use the default scoring scheme.
        Otherwise, the agent uses OpenAI to extract custom weights for 'rating', 'reviews', and 'price'.
        """
        user_input = input(
            "\nWhat do you care about most when choosing a product? "
            "Type 'default' for the default prioritization, or describe your priorities (e.g., 'I care more about reviews and rating than price'): "
        )
        if user_input.strip().lower() == "default":
            return "default", None
        else:
            prompt = (
                f"Based on the user's statement: '{user_input}', please provide a JSON object with keys 'rating', 'reviews', and 'price', "
                "where each value is a float weight indicating the importance of that factor in choosing a product. "
                "The weights should sum to 1. For example: {\"rating\": 0.3, \"reviews\": 0.5, \"price\": 0.2}."
            )
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You extract weightings for product factors from a user's statement."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    max_tokens=100,
                    response_format={"type": "json_object"}
                )
                answer = response.choices[0].message.content.strip()
                weights = json.loads(answer)
                logging.info("Custom weights extracted: %s", weights)
                return "custom", weights
            except Exception as e:
                logging.error("Error extracting custom weights: %s", e)
                return "default", None

    def score_product(self, product, weights=None):
        """
        Computes a score for a product using its rating, review count, and price.
        If weights is None, uses the default scheme:
             score = (rating * log(reviews + 1)) / price
        Otherwise, uses a weighted sum:
             score = (w_rating * rating) + (w_reviews * log(reviews + 1)) - (w_price * price)
        """
        rating = product.get("rating") if product.get("rating") is not None else 0
        reviews = product.get("reviews") if product.get("reviews") is not None else 0
        price = product.get("price") if product.get("price") is not None and product.get("price") > 0 else 1
        if weights is None:
            score = rating * math.log(reviews + 1) / price
        else:
            score = (weights.get("rating", 0) * rating) + (weights.get("reviews", 0) * math.log(reviews + 1)) - (weights.get("price", 0) * price)
        return score

    def decide_products(self, products, filters, weights=None):
        """
        Filters products based on the user's criteria, scores them (using default or custom weights),
        and returns the top 3 options.
        """
        filtered_products = []
        for product in products:
            if filters.get("max_price") and product.get("price") is not None:
                if product["price"] > filters["max_price"]:
                    continue
            if filters.get("min_rating") and product.get("rating") is not None:
                if product["rating"] < filters["min_rating"]:
                    continue
            if filters.get("prime") and not product.get("prime"):
                continue
            filtered_products.append(product)

        scored_products = []
        for product in filtered_products:
            score = self.score_product(product, weights)
            scored_products.append((score, product))

        scored_products.sort(key=lambda x: x[0], reverse=True)
        top_products = [p for score, p in scored_products[:3]]
        return top_products

    def fetch_product_page_details_by_click(self, element):
        """
        Uses Selenium to click on the given product element, waits for the product page to load,
        uses OpenAI to extract detailed information from the product page HTML,
        and then navigates back to the search results.
        """
        try:
            element.click()
            time.sleep(3)  # Wait for the product page to load.
            html = self.driver.page_source
            details = self.parse_detailed_product_info_with_openai(html)
            self.driver.back()
            time.sleep(3)
            return details
        except Exception as e:
            logging.error("Error fetching product page details by clicking: %s", e)
            return {"detailed_description": "Details not available", "key_features": []}

    def parse_detailed_product_info_with_openai(self, html):
        """
        Uses OpenAI to extract detailed product information from the HTML of a product page.
        Expected output is a JSON object with keys:
          - detailed_description (a brief summary of the product description)
          - key_features (a list of bullet points for product features)
        """
        prompt = (
            "Extract detailed product information from the following HTML of an Amazon product page.\n"
            "Return a JSON object with keys 'detailed_description' (a brief summary of the product description) "
            "and 'key_features' (a list of key features or bullet points) if available.\n"
            f"HTML: {html}"
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You extract detailed product information from an Amazon product page HTML."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            answer = response.choices[0].message.content.strip()
            details = json.loads(answer)
            return details
        except Exception as e:
            logging.error("OpenAI API error in parse_detailed_product_info_with_openai: %s", e)
            return {"detailed_description": "Details not available", "key_features": []}

    def should_browse_question(self, question, product):
        """
        Uses OpenAI to decide whether browsing to the product page would yield a better answer.
        Returns True if OpenAI's response contains "yes", else False.
        """
        context = (
            f"Product details: Title: {product.get('title')}, Price: {product.get('price')}, "
            f"Rating: {product.get('rating')}, Reviews: {product.get('reviews')}, Prime: {product.get('prime')}\n"
            f"Follow-up question: {question}\n"
            "Should browsing to the product page yield a better answer? Answer 'yes' or 'no'."
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Decide if browsing to the product page will yield a better answer."},
                    {"role": "user", "content": context}
                ],
                temperature=0.0,
                max_tokens=50
            )
            answer = response.choices[0].message.content.strip().lower()
            return "yes" in answer
        except Exception as e:
            logging.error("Error in should_browse_question: %s", e)
            return False

    def answer_question_with_details(self, question, product, details):
        """
        Uses OpenAI to answer a follow-up question using detailed product page information.
        """
        context = (
            f"Product details:\n"
            f"  Title   : {product.get('title')}\n"
            f"  Price   : {product.get('price')}\n"
            f"  Rating  : {product.get('rating')}\n"
            f"  Reviews : {product.get('reviews')}\n"
            f"  Prime   : {product.get('prime')}\n"
            f"Detailed info: {json.dumps(details, indent=2)}\n"
            f"Question: {question}\n"
            "Answer the question based on the detailed product information."
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Answer follow-up questions using detailed product information."},
                    {"role": "user", "content": context}
                ],
                temperature=0.0,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
            return answer
        except Exception as e:
            logging.error("Error in answer_question_with_details: %s", e)
            return "Sorry, I couldn't fetch detailed information."

    def answer_followup_question(self, question, products):
        """
        Uses OpenAI to answer a general follow-up question based on the summary of the product options.
        """
        context = "Here are the product options:\n"
        for idx, product in enumerate(products, start=1):
            context += (
                f"Option {idx}:\n"
                f"  Title   : {product.get('title')}\n"
                f"  Price   : {product.get('price')}\n"
                f"  Rating  : {product.get('rating')}\n"
                f"  Reviews : {product.get('reviews')}\n"
                f"  Prime   : {product.get('prime')}\n\n"
            )
        prompt = (
            f"Based on the following product options:\n{context}\n"
            f"Answer the following follow-up question:\n{question}\n"
            "Provide a clear and concise answer."
        )
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You answer follow-up questions about product search results."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
            return answer
        except Exception as e:
            logging.error("OpenAI API error in answer_followup_question: %s", e)
            return "Sorry, I could not process your question."

    def conversation_loop(self, products):
        """
        Engages in a Q&A conversation loop with the user.
        For each follow-up question, it first uses OpenAI to decide if clicking on the product in the search results
        will yield a better answer. If yes and the product element is available, it clicks that element to extract
        detailed info before answering.
        """
        print("\n--- Entering Follow-Up Conversation ---")
        while True:
            followup = input("\nEnter your follow-up question (or type 'exit' to finish): ").strip()
            if followup.lower() == "exit":
                print("\nThank you for using the Amazon Shopping Assistant. Goodbye!")
                break

            # Check if the user is asking for details about a specific option.
            option_number = None
            for i in range(1, 4):
                if f"option {i}" in followup.lower():
                    option_number = i
                    break

            if option_number is not None:
                product = products[option_number - 1]
                # Ask OpenAI if clicking on the product would yield a better answer.
                need_browse = self.should_browse_question(followup, product)
                if need_browse and product.get("element"):
                    details = self.fetch_product_page_details_by_click(product["element"])
                    answer = self.answer_question_with_details(followup, product, details)
                    print("\n=== Answer ===")
                    print(answer)
                    print("=" * 40)
                else:
                    answer = self.answer_followup_question(followup, products)
                    print("\n=== Answer ===")
                    print(answer)
                    print("=" * 40)
            else:
                answer = self.answer_followup_question(followup, products)
                print("\n=== Answer ===")
                print(answer)
                print("=" * 40)

    def run(self, query):
        """
        Runs the agent:
          1. Uses OpenAI to parse the natural language query.
          2. Performs the search on Amazon.
          3. Extracts product data by parsing the HTML of each product.
          4. Asks the user what they care about and, based on that, decides which products to show.
          5. Returns the top 3 product options.
        """
        logging.info("Starting the Amazon Shopping Assistant Agent")
        filters = self.parse_query_with_openai(query)
        self.perform_search(filters)
        products = self.extract_products()
        # Ask the user for their priorities.
        priority_type, weights = self.get_user_priority_weights()
        if priority_type == "default":
            weights = None
        top_products = self.decide_products(products, filters, weights)
        return top_products

    def shutdown(self):
        """Closes the Selenium driver."""
        self.driver.quit()


if __name__ == "__main__":
    # Ask the user what they want to buy.
    user_query = input("What are you looking to buy? ").strip()

    assistant = AmazonShoppingAssistant()
    try:
        results = assistant.run(user_query)
        print("\n" + "=" * 40)
        print("Top 3 Product Options:")
        print("=" * 40)
        for idx, product in enumerate(results, start=1):
            print(f"Option {idx}:")
            print(f"  Title   : {product.get('title')}")
            print(f"  Price   : {product.get('price')}")
            print(f"  Rating  : {product.get('rating')}")
            print(f"  Reviews : {product.get('reviews')}")
            print(f"  Prime   : {product.get('prime')}")
            print("-" * 40)
        print("\nThe top 3 product options are displayed above.")

        # Enter the follow-up conversation loop.
        assistant.conversation_loop(results)
    except Exception as e:
        logging.error("An error occurred during agent execution: %s", e)
    finally:
        assistant.shutdown()