<h1 align="center">Navigator</h1>

<p align="center">
  An agentic AI workflow that helps blind individuals manage their healthcare over the phone.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Built%20at-Anthropic%20Hackathon-1f6feb?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Top%205-Accessibility%20Category-2ea44f?style=for-the-badge" />
</p>

---

### What it is

Navigator is a social impact project built at an Anthropic hackathon. It helps blind individuals manage the parts of healthcare that are normally buried in inboxes and portals, things like appointment confirmations, insurance follow-ups, and action items from doctors, by turning them into simple phone calls.

### The problem

Healthcare admin leans heavily on visual interfaces: email threads, patient portals, PDFs. For blind individuals, that creates a real barrier between them and information that affects their care. Navigator removes the screen from the equation entirely.

### How it works

Navigator uses an agentic AI workflow to read through a user's healthcare-related emails, identify what actually matters (appointments, action items, follow-ups), and summarize it. It then uses ElevenLabs to place an outbound phone call to the user, reading the summary aloud in natural speech. From there, the user can respond by voice, and Navigator can carry out instructions on their behalf, like confirming an appointment or flagging something for a caregiver.

### Tech stack

<p>
  <img src="https://skillicons.dev/icons?i=python,js,nodejs" />
</p>

- **Agentic workflow** for parsing and summarizing emails and action items
- **ElevenLabs** for voice synthesis and outbound calling
- Built end-to-end during the hackathon timeframe

### Status

Navigator was built as a hackathon project focused on proving out the concept, and placed top 5 in the Accessibility category. It's not in active production use, but the core workflow (email parsing to voice call to instruction execution) is fully functional.
