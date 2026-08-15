import {
  type ChatMessage,
  type PromptPreprocessorController,
} from "@lmstudio/sdk";

export async function preprocess(
  _ctl: PromptPreprocessorController,
  userMessage: ChatMessage,
) {
  const now = new Date();
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown";
  const localDateTime = new Intl.DateTimeFormat(undefined, {
    dateStyle: "full",
    timeStyle: "long",
    timeZone,
  }).format(now);

  const runtimeContext = [
    '<runtime-context source="local-system-clock">',
    `Current local date and time: ${localDateTime}`,
    `Current UTC time: ${now.toISOString()}`,
    `Local IANA time zone: ${timeZone}`,
    "Treat these values as authoritative for this turn.",
    "Resolve today, tomorrow, yesterday, now, and latest relative to this date and time.",
    "Do not claim that your training cutoff is the current date.",
    "For facts that may have changed, use a research tool and prefer current primary sources.",
    "Do not call a time tool merely to re-check this successful clock injection.",
    "</runtime-context>",
  ].join("\n");

  userMessage.replaceText(`${runtimeContext}\n\n${userMessage.getText()}`);
  return userMessage;
}
