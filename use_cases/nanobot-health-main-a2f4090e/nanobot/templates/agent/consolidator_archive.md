Extract key facts from this conversation. Only output items matching these categories, skip everything else:
- User facts: personal info, preferences, stated opinions, habits
- Decisions: choices made, conclusions reached
- Solutions: working approaches discovered through trial and error, especially non-obvious methods that succeeded after failed attempts
- Events: plans, deadlines, notable occurrences
- Preferences: communication style, tool preferences
- Health data: medications, conditions, allergies, symptoms, vital signs, test results, lifestyle entries

Priority: user corrections and preferences > health data > solutions > decisions > events > environment facts. The most valuable memory prevents the user from having to repeat themselves.

When summarizing health information, preserve exact values:
- Medication names with exact doses and frequency (e.g., "Lisinopril 10mg daily")
- Vital signs and lab results with numbers (e.g., "BP 128/82", "A1c 6.5%")
- Allergy details including the reaction and severity
- Symptom descriptions with timing and duration

Skip: code patterns derivable from source, git history, or anything already captured in existing memory.

Output as concise bullet points, one fact per line. No preamble, no commentary.
If nothing noteworthy happened, output: (nothing)
