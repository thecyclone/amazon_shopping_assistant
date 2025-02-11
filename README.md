## Project setup

### 1. Make a virtual environment
```
python3 -m venv amazon_assistant
```

### 2. Install dependencies
```
pip install -r requirements.txt
```
(The codebase uses selenium so make sure you have google chrome installed on your machine)

### 3. Set up openAI key

#### Option 1: Export the OpenAI key
```
export OPENAI_API_KEY_PROBOOK="yourkey"
```

#### Option 2: Environment Variable using zsh
```
echo "export OPENAI_API_KEY_PROBOOK='yourkey'" >> ~/.zshrc
```

### 4. Running the agent
```
python3 main.py
```

### 5. Recovering from Selenium errors

In case you encounter a Selenium error, example:
```
 Error during search submission: Message: no such element: Unable to locate element: {"method":"css selector","selector":"[id="twotabsearchtextbox"]"}
```
please exit the program and re-start it. 