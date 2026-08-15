import {
  tool,
  type Tool,
  type ToolsProviderController,
} from "@lmstudio/sdk";

export async function toolsProvider(
  _ctl: ToolsProviderController,
): Promise<Tool[]> {
  const currentTimeTool = tool({
    name: "get_current_time",
    description:
      "Return the authoritative current date, time, UTC timestamp, and IANA time zone of the LM Studio host. Use this for questions involving now, today, tomorrow, yesterday, or the current time.",
    parameters: {},
    implementation: async () => {
      const now = new Date();
      const timeZone =
        Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown";
      const localDateTime = new Intl.DateTimeFormat(undefined, {
        dateStyle: "full",
        timeStyle: "long",
        timeZone,
      }).format(now);

      return {
        localDateTime,
        utcTime: now.toISOString(),
        timeZone,
        unixTimeMilliseconds: now.getTime(),
      };
    },
  });

  return [currentTimeTool];
}
