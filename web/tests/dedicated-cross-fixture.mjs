// Shared acceptance input: one qualified person, two overlapping flights,
// a dedicated source, and an explicit continuation to the other flight.
export function dedicatedCrossConfiguration() {
  const source='入境申报',target='登机人证核对/队伍秩序维护',person='跨班验证员';
  const roles=[source,target];
  const flights=[{id:'A',flightNo:'CX937',windowStart:'10:10',windowEnd:'11:30'},
    {id:'B',flightNo:'KE0166',windowStart:'11:00',windowEnd:'12:15'}];
  const handoffPreferences=[{flightNo:'*',arrivalRole:source,boardingRoles:[target],
    targetFlightScopes:{[target]:'later-overlap'},enabled:true,ordered:true}];
  const workRules={pairs:[],baggage:[],separationGroups:[],forbiddenHandoffs:[]};
  return {rotationEnabled:false,rotationMinutes:60,handoffPreferences,workRules,
    day:{serviceDate:'2058-08-24',title:'专岗跨航班接续验收',flights,roleNames:roles,remarks:[],roster:[person],
      remarksReviewed:true,transitionMinutes:0,releasePolicy:'task',attendanceMode:'duty',
      dedicatedFlightProtection:true,flowPreferences:handoffPreferences,workRules,
      roleConfigs:roles.map(roleName=>({roleName,requiredCount:0,workMode:roleName===source?'arrival':'boarding',
        exclusiveSameFlight:roleName===source,concurrent:roleName!==source})),
      memberConfigs:[{memberName:person,eligibleRoleNames:roles}],
      memberAvailability:[{memberName:person,participating:true}],extraOccupancies:[],
      cells:flights.flatMap(f=>roles.map(roleName=>({id:f.id+roleName,flightId:f.id,flightNo:f.flightNo,roleName,
        rawValue:'',internalNames:[],externalLabels:[],semantics:'empty',
        requiredCountOverride:Number(f.id==='A'?roleName===source:roleName===target)})))} };
}
