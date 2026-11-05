import { CpModel, CpSolver, CpSolverSolutionCallback, LinearExpr } from "cp-sat-wasm";

type Expression = { terms: [number, string][]; offset: string };
type Model = { variables: [string, string, string][]; constraints: unknown[][]; objective: Expression; parameters: Record<string, number> };
let activeSolver: CpSolver | null = null;
export function interruptCp(): void { activeSolver?.stopSearch(); }

export async function solveCp(raw: string, solution: (values: string) => boolean): Promise<string> {
  const input = JSON.parse(raw) as Model;
  const model = new CpModel();
  const variables = input.variables.map(([lo, hi, name]) => model.newIntVar(BigInt(lo), BigInt(hi), name));
  const expr = (data: Expression): LinearExpr => {
    let value = LinearExpr.from(BigInt(data.offset));
    for (const [index, coefficient] of data.terms) value = value.plus(variables[index].times(BigInt(coefficient)));
    return value;
  };
  for (const [kind, left, right, extra] of input.constraints) {
    if (kind === "bool") model.add(left as boolean);
    else if (kind === "eq") model.add(expr(left as Expression).eq(0n));
    else if (kind === "le") model.add(expr(left as Expression).le(0n));
    else if (kind === "ge") model.add(expr(left as Expression).ge(0n));
    else if (kind === "max") model.addMaxEquality(expr(left as Expression), (right as Expression[]).map(expr));
    else if (kind === "min") model.addMinEquality(expr(left as Expression), (right as Expression[]).map(expr));
    else if (kind === "element") model.addElement(expr(extra as Expression), (right as Expression[]).map(expr), expr(left as Expression));
    else throw new Error("不支持的排班模型约束：" + kind);
  }
  model.minimize(expr(input.objective));
  const solver = new CpSolver();
  class Candidate extends CpSolverSolutionCallback {
    onSolutionCallback(): void {
      if (solution(JSON.stringify(variables.map(variable => String(this.value(variable))))) === true) solver.stopSearch();
    }
  }
  activeSolver = solver;
  try {
    const status = await solver.solve(model, {executor: "worker", numWorkers: input.parameters.num_search_workers ?? 0,
      randomSeed: input.parameters.random_seed ?? 0, maxTimeInSeconds: input.parameters.max_time_in_seconds ?? 5,
      solutionCallback: new Candidate()});
    const names: Record<string, number> = {UNKNOWN: 0, MODEL_INVALID: 1, FEASIBLE: 2, INFEASIBLE: 3, OPTIMAL: 4};
    const label = String(status ?? "UNKNOWN");
    return JSON.stringify({status: names[label] ?? 0,
      solution: label === "OPTIMAL" || label === "FEASIBLE" ? variables.map(variable => String(solver.value(variable))) : []});
  } finally { activeSolver = null; }
}
