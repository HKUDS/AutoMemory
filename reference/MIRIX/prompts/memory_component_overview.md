# Six Memory Components Overview

Classification rules for routing content to specialized memory types.

**Source:** `mirix/prompts/system/meta_memory_agent.txt`

## 1. Core Memory

**Purpose:** WHO the user is and HOW to interact with them effectively.

**Contains:** User identity, personality traits, communication style preferences, relationship information, behavioral patterns.

**Examples:** "User's name is Alex, prefers direct communication"; "Prefers final responses only, doesn't want intermediate messages".

**Rule:** Update when you identify ANY personal information, preference, or trait that affects how you should interact with the user.

---

## 2. Episodic Memory

**Purpose:** WHAT happened WHEN in the user's life.

**Contains:** Specific events, activities, conversations, experiences with timestamps.

**Examples:** "Had dinner with Sarah, discussed career changes"; "Completed project presentation, received positive feedback".

**Rule:** Update for virtually ALL user activities and experiences. Episodic almost always needs updates.

---

## 3. Procedural Memory

**Purpose:** HOW to do specific tasks or follow procedures.

**Contains:** Workflows, tutorials, step-by-step guides, repeatable processes.

**Examples:** "How to reset router: 1. Unplug 2. Wait 10s 3. Plug back in"; "Daily morning routine: 1. Check emails 2. Review calendar".

**Rule:** Update when messages contain sequential steps, workflows, or instructional content.

---

## 4. Resource Memory

**Purpose:** WHAT files and documents are available for reference.

**Contains:** Shared documents, images, files, reference materials.

**Examples:** "ProjectPlan.docx - contains Q1 roadmap"; "Screenshot of error message".

**Rule:** Update when user shares ANY file, document, image, or references specific materials. Update existing entry when same document.

---

## 5. Knowledge Vault

**Purpose:** WHERE to find specific factual information when needed.

**Contains:** Contact details, passwords, addresses, IDs, static reference data.

**Examples:** "Doctor's office: (555) 123-4567"; "WiFi password: SecureHome2025!"; "Employee ID: EMP-2024-7891".

**Rule:** Update when messages contain specific factual data for future lookup. Not user preferences (use Core for those).

---

## 6. Semantic Memory

**Purpose:** WHAT things mean and general knowledge about the world.

**Contains:** Definitions, concepts, general facts about people/places/things, abstract knowledge.

**Examples:** "MemoryLLM - AI architecture using episodic memory"; "Jane Smith - project manager at TechCorp, expert in agile"; "Mom - retired teacher, lives in Portland".

**Rule:** Update when you encounter NEW concepts, people, places, organizations. Not generic terms or filenames.

**Distinguish from Core:** Core = HOW person affects user preferences; Semantic = WHO someone is, what they do.
