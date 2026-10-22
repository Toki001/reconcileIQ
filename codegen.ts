import type { CodegenConfig } from "@graphql-codegen/cli";

const config: CodegenConfig = {
  schema: "packages/contracts/schema.graphql",
  documents: ["packages/contracts/operations/*.graphql"],
  generates: {
    "packages/generated/src/graphql.ts": {
      plugins: ["typescript-operations", "typed-document-node"],
      config: {
        useTypeImports: true,
        avoidOptionals: true,
        strictScalars: true,
        scalars: { Date: "string", DateTime: "string", MinorUnits: "number" },
      },
    },
  },
};
export default config;
