** Objective value: 1026.0

** Placement Result **
Name      X     Y  Flip  Width  Height  SrcCol  SrcNet  DrnCol  DrnNet  GCol  GNet  Model
---------------------------------------------------------------------------------------
MM0S0  42.0   0.0    NF   84.0    48.0    42.0      ZN    -1.0     VSS  84.0     I   NMOS
MM1S0  42.0  96.0    NF   84.0    48.0    42.0      ZN    -1.0     VDD  84.0     I   PMOS

** Cell Information **
IO Pins
----------------------
I ZN

** Routing Result **
MET   ROW   COL  NET      MET    ROW    COL  NET
------------------------------------------------
0     0.0  42.0   ZN  =>  1      0.0   42.0   ZN
0    48.0  84.0    I  =>  1     48.0   84.0    I
1     0.0   0.0   ZN  =>  1      0.0   42.0   ZN
1     0.0   0.0   ZN  =>  2      0.0    0.0   ZN
1    48.0  84.0    I  =>  1     48.0  126.0    I
1    48.0  84.0    I  =>  2     48.0   84.0    I
2     0.0   0.0   ZN  =>  2    144.0    0.0   ZN
2     0.0  84.0    I  =>  2    144.0   84.0    I

** Technology Parameters **
Name                           Value
----------------------------------------
COL                                2
TRACK                              4
CPP                             42.0
M0P                             24.0
M1P                             42.0
M2P                             24.0
CP_WIDTH                        14.0
M0_WIDTH                        12.0
M1_WIDTH                        14.0
M2_WIDTH                        12.0
ACTIVE_GAP                      14.0
PWR_RAIL_THICKNESS              26.0
PWR_CONFIG                     M0BPR
