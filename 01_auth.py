import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def main():
    if not os.path.exists("client_secret.json"):
        raise FileNotFoundError("Missing client_secret.json in project folder")

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    with open("token.json", "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print("✅ Auth complete. token.json created.")

if __name__ == "__main__":
    main()