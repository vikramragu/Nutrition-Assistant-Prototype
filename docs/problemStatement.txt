# Problem Statement — AI Nutrition Assistant Prototype

## Objective

Build an AI nutrition assistant prototype that answers food, nutrition, and food-safety questions using structured responses, enforces explicit safety boundaries in code, and records unsupported or inconsistent model claims for future improvement.

The prototype should establish the response and evaluation contract needed for a later milestone in which claims can be backed by sources.

---

## 1. Chat Frontend

Build a simple chat interface containing:

- A message list showing the conversation.
- An input box for submitting questions.
- A sources panel displayed next to the conversation.

The sources panel should be implemented now but remain empty for this milestone. A later milestone will populate it with actual sources.

---

## 2. Backend

Build a backend that provides:

- A chat endpoint.
- A mechanism for storing the conversation.
- A server-side model call.

The model API must **not** be called directly from the browser. All model calls must go through the backend.

Suggested stack:

- Next.js or React for the frontend.
- FastAPI for the backend.
- Supabase, Postgres, or SQLite for storage.

---

## 3. Structured Response Schema

The model must return structured output rather than free-form prose.

The response schema must contain:

- `answer`: The assistant's answer text.
- `claims`: A list of claims made in the answer.

Each claim must contain:

- `claim`: The claim text.
- `source`: The source associated with the claim.

For this milestone:

- Every `source` value must be `null`.
- Do not invent or fabricate sources.
- The schema should be designed so that a future milestone can populate the source field without changing the overall contract.

The backend must parse and validate every model response against the schema.

If the model response does not conform to the schema, the application must treat it as a parsing/validation failure rather than attempting to extract or repair prose manually.

Use the structured-output capabilities provided by the selected model API.

---

## 4. System Prompt

Create a dedicated system prompt that clearly defines:

### Assistant purpose

Describe what the nutrition assistant is designed to help with, including food, nutrition, and food-safety questions.

### Response behavior

Specify:

- How the assistant should answer questions.
- How concise or detailed responses should be.
- How claims should be represented.
- How uncertainty should be communicated.

### Safety boundaries

Clearly specify what the assistant must not provide.

The prompt should support the code-level safety checks described below, but the prompt alone must not be considered sufficient enforcement.

### Regression questions

Maintain a fixed test set of questions.

After every system-prompt change:

1. Run the complete fixed question set.
2. Review the outputs.
3. Check whether the change fixed the intended behavior.
4. Check whether it introduced regressions in other questions.

The goal is to prevent fixing one case while silently breaking several others.

---

## 5. Scope Limits — Enforced in Code

The assistant must not provide:

- Calorie targets or calorie prescriptions.
- Weight targets.
- Recommendations about what a person should weigh.
- Medical advice.

When a user asks for information that falls into these prohibited areas, the application should decline the request and direct the user toward an appropriately qualified professional.

These restrictions must be enforced in application code and must not rely solely on the system prompt.

The code-level check should occur before the request is allowed to proceed to the model when practical.

---

## 6. Failure Log

Create a fixed evaluation set of **10 questions** covering the following four categories:

1. Nutrient requirements.
2. Food safety and storage.
3. Cooking methods.
4. Questions where there is no clear or universally accepted answer.

Run all 10 questions against the assistant.

For every response, inspect and record the following failure types:

### Unsupported claims

Claims stated as facts without adequate support or justification.

### Inconsistent numbers

Numerical values or quantitative claims that change between runs without a meaningful reason.

### Unverifiable sources

Sources cited by the model that cannot be located or verified.

For this milestone, source fields are required to remain `null`; therefore, any fabricated source information should be treated as a failure.

### Missed scope restrictions

Questions that should have been declined but received prohibited calorie, weight-target, or medical advice.

### Unhelpful hedging

Responses that hedge so heavily that they fail to provide useful information when a reasonable, qualified answer could have been given.

Group failures by type and count the occurrences.

The failure log should be retained as an artifact that can be reviewed and used to improve the system prompt, application logic, and evaluation process.

---

## 7. Deployment

Push the completed project to GitHub.

Deploy the application so that it is accessible through a public URL.

Supported deployment options:

- Vercel
- Railway

The final deployed application must be publicly accessible and usable without requiring a local development environment.

---

## 8. Technical Requirements

### Model

Use either:

- OpenAI API
- Anthropic API

Use the provider's structured-output capability rather than asking the model for prose and manually parsing it.

### Frontend and Backend

Use:

- Next.js or React
- FastAPI

The exact frontend/backend combination may be chosen based on implementation preference.

### Storage

Use one of:

- Supabase
- Postgres
- SQLite

Store enough conversation information to support the chat experience and future development.

### Scaffolding / Development Tools

Tools such as Cursor or Antigravity may be used for project scaffolding and development.

---

## 9. Acceptance Criteria

The prototype is complete when all of the following are true:

- [ ] A working chat frontend exists.
- [ ] The frontend contains a message list and input box.
- [ ] A sources panel exists beside the conversation.
- [ ] The sources panel is empty for this milestone.
- [ ] A backend chat endpoint exists.
- [ ] Conversation data can be stored.
- [ ] Model calls happen on the backend, never directly in the browser.
- [ ] Model responses use a defined structured schema.
- [ ] The schema contains an answer field.
- [ ] The schema contains a claims list.
- [ ] Every claim contains claim text and a source field.
- [ ] Every source field is `null`.
- [ ] Responses are validated against the schema.
- [ ] Invalid structured responses cause a failure rather than being parsed as free-form prose.
- [ ] A dedicated system prompt defines the assistant's purpose, behavior, response length, and boundaries.
- [ ] A fixed set of regression questions exists.
- [ ] The regression set is rerun after system-prompt changes.
- [ ] Calorie/weight targets are blocked.
- [ ] Recommendations about what someone should weigh are blocked.
- [ ] Medical advice is blocked.
- [ ] Scope restrictions are implemented in code, not only in the prompt.
- [ ] Ten evaluation questions have been created across the four required categories.
- [ ] All ten questions have been run.
- [ ] Failures have been recorded.
- [ ] Failures have been grouped and counted.
- [ ] The project is pushed to GitHub.
- [ ] The application is deployed to Vercel or Railway.
- [ ] The deployed application is accessible through a public URL.

---

## 10. Core Rule

The prototype should prioritize a reliable contract over clever model behavior:

> Every response must conform to the schema, every claim must contain a `source` field whose value is `null` for this milestone, safety boundaries must be enforced in code, model calls must remain behind the backend, and failures must be recorded rather than silently patched around.
