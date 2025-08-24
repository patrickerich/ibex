// integration/wrappers/ibex_wrapper.sv
// Thin parametric wrapper that forwards *all* Ibex parameters and ports.
//
// Notes:
// - Mirrors ibex_top's parameter list and defaults exactly.
// - Keeps RVFI ports under `ifdef RVFI` like ibex_top.
// - Uses named-connections with (.*) since names match 1:1.

module ibex_wrapper
  import ibex_pkg::*;
#(
  // ---------------- PMPs / Counters / Resets ----------------
  parameter bit                     PMPEnable                    = 1'b0,
  parameter int unsigned            PMPGranularity               = 0,
  parameter int unsigned            PMPNumRegions                = 4,
  parameter int unsigned            MHPMCounterNum               = 0,
  parameter int unsigned            MHPMCounterWidth             = 40,
  parameter ibex_pkg::pmp_cfg_t     PMPRstCfg[16]                = ibex_pkg::PmpCfgRst,
  parameter logic [33:0]            PMPRstAddr[16]               = ibex_pkg::PmpAddrRst,
  parameter ibex_pkg::pmp_mseccfg_t PMPRstMsecCfg                = ibex_pkg::PmpMseccfgRst,

  // ---------------- Core feature selection -------------------
  parameter bit                     RV32E                        = 1'b0,
  parameter rv32m_e                 RV32M                        = RV32MFast,
  parameter rv32b_e                 RV32B                        = RV32BNone,
  parameter regfile_e               RegFile                      = RegFileFF,
  parameter bit                     BranchTargetALU              = 1'b0,
  parameter bit                     WritebackStage               = 1'b0,
  parameter bit                     ICache                       = 1'b0,
  parameter bit                     ICacheECC                    = 1'b0,
  parameter bit                     BranchPredictor              = 1'b0,
  parameter bit                     DbgTriggerEn                 = 1'b0,
  parameter int unsigned            DbgHwBreakNum                = 1,
  parameter bit                     SecureIbex                   = 1'b0,

  // ---------------- ICache scrambling ------------------------
  parameter bit                     ICacheScramble               = 1'b0,
  parameter int unsigned            ICacheScrNumPrinceRoundsHalf = 2,

  // ---------------- Random constants -------------------------
  parameter lfsr_seed_t             RndCnstLfsrSeed              = RndCnstLfsrSeedDefault,
  parameter lfsr_perm_t             RndCnstLfsrPerm              = RndCnstLfsrPermDefault,

  // ---------------- Debug module address map -----------------
  parameter int unsigned            DmBaseAddr                   = 32'h1A110000,
  parameter int unsigned            DmAddrMask                   = 32'h00000FFF,
  parameter int unsigned            DmHaltAddr                   = 32'h1A110800,
  parameter int unsigned            DmExceptionAddr              = 32'h1A110808,

  // ---------------- Scrambling key/nonce defaults ------------
  parameter logic [SCRAMBLE_KEY_W-1:0]   RndCnstIbexKey          = RndCnstIbexKeyDefault,
  parameter logic [SCRAMBLE_NONCE_W-1:0] RndCnstIbexNonce        = RndCnstIbexNonceDefault,

  // ---------------- CSR identification -----------------------
  parameter logic [31:0]            CsrMvendorId                 = 32'b0,
  parameter logic [31:0]            CsrMimpId                    = 32'b0
) (
  // ---------------- Clocks / reset / test --------------------
  input  logic                         clk_i,
  input  logic                         rst_ni,
  input  logic                         test_en_i,     // enable all clock gates for testing
  input  prim_ram_1p_pkg::ram_1p_cfg_t ram_cfg_i,

  // ---------------- Boot / identity --------------------------
  input  logic [31:0]                  hart_id_i,
  input  logic [31:0]                  boot_addr_i,

  // ---------------- Instruction interface -------------------
  output logic                         instr_req_o,
  input  logic                         instr_gnt_i,
  input  logic                         instr_rvalid_i,
  output logic [31:0]                  instr_addr_o,
  input  logic [31:0]                  instr_rdata_i,
  input  logic [6:0]                   instr_rdata_intg_i,
  input  logic                         instr_err_i,

  // ---------------- Data interface ---------------------------
  output logic                         data_req_o,
  input  logic                         data_gnt_i,
  input  logic                         data_rvalid_i,
  output logic                         data_we_o,
  output logic [3:0]                   data_be_o,
  output logic [31:0]                  data_addr_o,
  output logic [31:0]                  data_wdata_o,
  output logic [6:0]                   data_wdata_intg_o,
  input  logic [31:0]                  data_rdata_i,
  input  logic [6:0]                   data_rdata_intg_i,
  input  logic                         data_err_i,

  // ---------------- Interrupts -------------------------------
  input  logic                         irq_software_i,
  input  logic                         irq_timer_i,
  input  logic                         irq_external_i,
  input  logic [14:0]                  irq_fast_i,
  input  logic                         irq_nm_i,       // non-maskable interrupt

  // ---------------- ICache Scrambling I/F --------------------
  input  logic                         scramble_key_valid_i,
  input  logic [SCRAMBLE_KEY_W-1:0]    scramble_key_i,
  input  logic [SCRAMBLE_NONCE_W-1:0]  scramble_nonce_i,
  output logic                         scramble_req_o,

  // ---------------- Debug / crash dump -----------------------
  input  logic                         debug_req_i,
  output crash_dump_t                  crash_dump_o,
  output logic                         double_fault_seen_o,

  // ---------------- RVFI (guarded) ---------------------------
`ifdef RVFI
  output logic                         rvfi_valid,
  output logic [63:0]                  rvfi_order,
  output logic [31:0]                  rvfi_insn,
  output logic                         rvfi_trap,
  output logic                         rvfi_halt,
  output logic                         rvfi_intr,
  output logic [ 1:0]                  rvfi_mode,
  output logic [ 1:0]                  rvfi_ixl,
  output logic [ 4:0]                  rvfi_rs1_addr,
  output logic [ 4:0]                  rvfi_rs2_addr,
  output logic [ 4:0]                  rvfi_rs3_addr,
  output logic [31:0]                  rvfi_rs1_rdata,
  output logic [31:0]                  rvfi_rs2_rdata,
  output logic [31:0]                  rvfi_rs3_rdata,
  output logic [ 4:0]                  rvfi_rd_addr,
  output logic [31:0]                  rvfi_rd_wdata,
  output logic [31:0]                  rvfi_pc_rdata,
  output logic [31:0]                  rvfi_pc_wdata,
  output logic [31:0]                  rvfi_mem_addr,
  output logic [ 3:0]                  rvfi_mem_rmask,
  output logic [ 3:0]                  rvfi_mem_wmask,
  output logic [31:0]                  rvfi_mem_rdata,
  output logic [31:0]                  rvfi_mem_wdata,
  output logic [31:0]                  rvfi_ext_pre_mip,
  output logic [31:0]                  rvfi_ext_post_mip,
  output logic                         rvfi_ext_nmi,
  output logic                         rvfi_ext_nmi_int,
  output logic                         rvfi_ext_debug_req,
  output logic                         rvfi_ext_debug_mode,
  output logic                         rvfi_ext_rf_wr_suppress,
  output logic [63:0]                  rvfi_ext_mcycle,
  output logic [31:0]                  rvfi_ext_mhpmcounters [10],
  output logic [31:0]                  rvfi_ext_mhpmcountersh [10],
  output logic                         rvfi_ext_ic_scr_key_valid,
  output logic                         rvfi_ext_irq_valid,
`endif

  // ---------------- Control / status / alerts ----------------
  input  ibex_mubi_t                   fetch_enable_i,
  output logic                         alert_minor_o,
  output logic                         alert_major_internal_o,
  output logic                         alert_major_bus_o,
  output logic                         core_sleep_o,

  // ---------------- DFT bypass controls ----------------------
  input  logic                         scan_rst_ni
);

  // Direct pass-through instantiation
  ibex_top #(
    .PMPEnable                    (PMPEnable),
    .PMPGranularity               (PMPGranularity),
    .PMPNumRegions                (PMPNumRegions),
    .MHPMCounterNum               (MHPMCounterNum),
    .MHPMCounterWidth             (MHPMCounterWidth),
    .PMPRstCfg                    (PMPRstCfg),
    .PMPRstAddr                   (PMPRstAddr),
    .PMPRstMsecCfg                (PMPRstMsecCfg),

    .RV32E                        (RV32E),
    .RV32M                        (RV32M),
    .RV32B                        (RV32B),
    .RegFile                      (RegFile),
    .BranchTargetALU              (BranchTargetALU),
    .WritebackStage               (WritebackStage),
    .ICache                       (ICache),
    .ICacheECC                    (ICacheECC),
    .BranchPredictor              (BranchPredictor),
    .DbgTriggerEn                 (DbgTriggerEn),
    .DbgHwBreakNum                (DbgHwBreakNum),
    .SecureIbex                   (SecureIbex),

    .ICacheScramble               (ICacheScramble),
    .ICacheScrNumPrinceRoundsHalf (ICacheScrNumPrinceRoundsHalf),

    .RndCnstLfsrSeed              (RndCnstLfsrSeed),
    .RndCnstLfsrPerm              (RndCnstLfsrPerm),

    .DmBaseAddr                   (DmBaseAddr),
    .DmAddrMask                   (DmAddrMask),
    .DmHaltAddr                   (DmHaltAddr),
    .DmExceptionAddr              (DmExceptionAddr),

    .RndCnstIbexKey               (RndCnstIbexKey),
    .RndCnstIbexNonce             (RndCnstIbexNonce),

    .CsrMvendorId                 (CsrMvendorId),
    .CsrMimpId                    (CsrMimpId)
  ) u_ibex_top (.*);

endmodule
