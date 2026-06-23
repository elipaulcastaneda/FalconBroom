.PHONY: gen-session-key

# Generate a URL-safe base64 key for Fernet (SESSION_ENCRYPTION_KEY)
# Usage: `make gen-session-key`
gen-session-key:
	python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
