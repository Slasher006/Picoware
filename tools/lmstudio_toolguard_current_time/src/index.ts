import { type PluginContext } from "@lmstudio/sdk";
import { preprocess } from "./promptPreprocessor";
import { toolsProvider } from "./toolsProvider";

export async function main(context: PluginContext) {
  context.withPromptPreprocessor(preprocess);
  context.withToolsProvider(toolsProvider);
}
