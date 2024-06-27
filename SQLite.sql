CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    discord_id VARCHAR(30) NOT NULL,
    username VARCHAR(100),
    verification_status BOOLEAN NOT NULL DEFAULT FALSE,
    last_verification_attempt TIMESTAMP
);

CREATE TABLE servers (
    id SERIAL PRIMARY KEY,
    server_id VARCHAR(30) NOT NULL,
    owner_id VARCHAR(30) NOT NULL,
    role_id VARCHAR(30) NOT NULL,
    tier VARCHAR(1) NOT NULL DEFAULT 'A',
    subscription_status BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE command_usage (
    id SERIAL PRIMARY KEY,
    server_id VARCHAR(30) NOT NULL,
    user_id VARCHAR(30) NOT NULL,
    command VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP NOT NULL
);
