## ADDED Requirements

### Requirement: Feedback controls on final Telegram DM responses
The system SHALL attach response-feedback controls only to final Telegram direct-message responses when the feature is explicitly enabled.

#### Scenario: Enabled final response
- **WHEN** a final response is delivered in a Telegram direct message and `feedback_buttons` is enabled
- **THEN** the system presents controls for useful, incorrect, slow, and unnecessary-internet feedback on the final message

#### Scenario: Disabled or non-final response
- **WHEN** feedback is disabled or a Telegram message is an intermediate/status message
- **THEN** the system delivers the message without response-feedback controls

#### Scenario: Group response
- **WHEN** a final response is delivered to a Telegram group, supergroup, or channel
- **THEN** the system does not attach response-feedback controls

### Requirement: Owner and lane bound feedback
The system MUST accept feedback only from the user, chat, topic, and message bound to the displayed controls and MUST accept at most one choice for a control instance.

#### Scenario: Valid owner selection
- **WHEN** the bound direct-message user selects one feedback option on the original response in the original topic
- **THEN** the system records exactly one feedback event and removes the controls

#### Scenario: Foreign or stale selection
- **WHEN** a different user, chat, topic, message, expired nonce, or already-consumed control submits a callback
- **THEN** the system rejects the callback and records no feedback event

### Requirement: Private local feedback journal
The system SHALL store accepted feedback locally in the active Hermes profile and MUST NOT store request text, response text, raw user id, raw chat id, session key, or topic id in the feedback event.

#### Scenario: Accepted feedback is stored minimally
- **WHEN** an authorized feedback selection is accepted
- **THEN** one JSONL record contains only the event timestamp, platform, feedback category, and whether the response belonged to a topic

#### Scenario: Journal write failure
- **WHEN** the local feedback journal cannot be written
- **THEN** the callback is acknowledged without disrupting the gateway or modifying the delivered response

### Requirement: Feedback path is delivery-safe
The system MUST treat feedback-control failures as non-fatal to response delivery.

#### Scenario: Telegram rejects reply-markup attachment
- **WHEN** the response itself is delivered but Telegram rejects attaching feedback controls
- **THEN** the original response remains a successful delivery and the failure is logged without retrying the response body
