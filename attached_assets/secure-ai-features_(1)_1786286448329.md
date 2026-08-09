---
name: Secure AI features against prompt injection
description: Use when building any feature that sends user input or external content (web pages, files, emails, documents) to an LLM — chatbots, AI assistants, summarizers, RAG, or agents that can call tools or take actions. Defends against prompt injection and excessive agency.
---

**Activation:** On-demand — fires when building an LLM-powered feature. Guardrail + actionable: Agent applies these defenses in the app code; some (human-in-the-loop approval) are deliberate design choices to confirm with the user.

# Instructions

When an app feeds user input or external content into an LLM, that input can hijack the model's behavior (prompt injection) — leaking data, bypassing rules, or triggering unauthorized actions. This is OWASP LLM01, and it is increasingly common as orgs build AI features. No method fully prevents it, so layer these mitigations.

- Constrain model behavior: in the system prompt, state the model's role, allowed tasks, and limits; tell it to stay on task and to ignore attempts to change its core instructions. Don't put secrets or privileged instructions where user content can reach them.
- Validate output, don't trust it: define the expected output format and check it with deterministic code before acting on it. Never pass model output straight into a shell, SQL, eval, or the DOM without validation/sanitization (this also covers OWASP LLM05 Improper Output Handling).
- Enforce least privilege: give the application its own API tokens and implement privileged functions in code, not by handing the model broad capabilities. Restrict the model's access (data, tools, scopes) to the minimum it needs (guards against Excessive Agency, LLM06).
- Require human approval for high-risk or irreversible actions (sending money, deleting data, emailing customers) — human-in-the-loop, not autonomous.
- Segregate and mark external/untrusted content (web pages, uploaded files, third-party data) so the model treats it as data, not instructions — indirect injection hides instructions in that content.
- Don't leak the system prompt or infrastructure details in responses (LLM07).
- Test adversarially: try to make the feature ignore its instructions, exfiltrate data, or call tools it shouldn't, before shipping. Treat the model as an untrusted user.

When building an AI feature, state which of these defenses are in place, especially least-privilege tool access and human approval for risky actions. Source: OWASP LLM01 Prompt Injection (genai.owasp.org/llmrisk/llm01-prompt-injection).
