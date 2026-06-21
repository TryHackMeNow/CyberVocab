# CyberVocab

A vocabulary-browser frontend with convenient query, update, import and export functionality.
Claude can be called for definitions of terms not contained within the data store. 
Automatically suggests new terms to be added via Google's Suggestions-API.

![Screenshot](./Screenshot.png)

# Installation

`pip install -r requirements.txt` then

`python3 main.py`

It does not get simpler than this.

For the AI-lookup-feature, just must have an anthropic API key and the environment variable ANTHROPIC_API_KEY must be set accordingly.

# Contributions

The user interface was build using [NiceGUI](https://nicegui.io).
Icons were designed in agentic co-work with Claude.

# License

MIT-License

