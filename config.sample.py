INSTANCES = {
        "instance_a": {
            "base_url": "https://jira.example.com/",
            "oauth": {
                "client_key": "addon-compare",
                "rsa_key": open("private_key.pem").read(),
            },
        },
        "instance_b": {
            "base_url": "https://jira-test.example.com/",
            "oauth": {
                "client_key": "addon-compare",
                "rsa_key": open("private_key.pem").read(),
            },
        }
}
