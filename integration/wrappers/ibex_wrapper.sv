// ----------------------------------------------------------------------------
// ibex_wrapper.sv
// A comprehensive, parameter-forwarding wrapper for Ibex.
// Exposes the native instruction/data memory interfaces plus IRQs, debug, etc.
// Delete what you don't need; all signals are straight through.
// ----------------------------------------------------------------------------
module ibex_wrapper
  import ibex_pkg::*;
#(
  // Forwarded Ibex parameters (must match your .core parameters)
  parameter int unsigned RV32E             = 0,
  parameter rv32m_e      RV32M             = ibex_pkg::RV32MFast,
  parameter rv32b_e      RV32B             = ibex_pkg::RV32BNone,
  parameter regfile_e    RegFile           = ibex_pkg::RegFileFF,
  parameter bit          ICache            = 0,
  parameter bit          ICacheECC         = 0,
  parameter bit          ICacheScramble    = 0,
  parameter bit          BranchTargetALU   = 0,
  parameter bit          WritebackStage    = 0,
  parameter bit          BranchPredictor   = 0,
  parameter bit          DbgTriggerEn      = 0,
  parameter bit          SecureIbex        = 0,
  parameter bit          PMPEnable         = 0,
  parameter int unsigned PMPGranularity    = 0,
  parameter int unsigned PMPNumRegions     = 4,
  parameter int unsigned MHPMCounterNum    = 0,
  parameter int unsigned MHPMCounterWidth  = 40
) (
  // Clock / Reset
  input  logic        clk_i,
  input  logic        rst_ni,

  // --------------------------------------------------------------------------
  // Core bring-up / test
  // --------------------------------------------------------------------------
  input  logic        test_en_i,        // tie 1'b0 if unused
  input  logic        fetch_enable_i,   // typically 1'b1 after reset
  input  logic [31:0] boot_addr_i,      // reset PC (aligned)
  input  logic [31:0] hart_id_i,        // hart ID CSR

  // --------------------------------------------------------------------------
  // Instruction memory interface (request/gnt/rvalid protocol)
  // --------------------------------------------------------------------------
  output logic        instr_req_o,
  input  logic        instr_gnt_i,
  input  logic        instr_rvalid_i,
  output logic [31:0] instr_addr_o,
  input  logic [31:0] instr_rdata_i,
  input  logic        instr_err_i,      // error from I-mem (bus error/ECC), tie 1'b0 if unused

  // --------------------------------------------------------------------------
  // Data memory interface (request/gnt/rvalid protocol)
  // --------------------------------------------------------------------------
  output logic        data_req_o,
  input  logic        data_gnt_i,
  input  logic        data_rvalid_i,
  output logic        data_we_o,
  output logic [3:0]  data_be_o,
  output logic [31:0] data_addr_o,
  output logic [31:0] data_wdata_o,
  input  logic [31:0] data_rdata_i,
  input  logic        data_err_i,       // error from D-mem, tie 1'b0 if unused

  // --------------------------------------------------------------------------
  // Interrupts
  // --------------------------------------------------------------------------
  input  logic        irq_software_i,
  input  logic        irq_timer_i,
  input  logic        irq_external_i,
  input  logic [14:0] irq_fast_i,       // fast IRQs [14:0]
  input  logic        irq_nm_i,         // non-maskable interrupt

  // --------------------------------------------------------------------------
  // Debug
  // --------------------------------------------------------------------------
  input  logic        debug_req_i,      // external debug request

  // --------------------------------------------------------------------------
  // Alerts / status (useful for power gating / clocking)
  // --------------------------------------------------------------------------
  output logic        core_sleep_o      // asserted when WFI or idle
`ifdef RVFI
  // --------------------------------------------------------------------------
  // RVFI (only when compiling with +define+RVFI or param RVFI enable in .core)
  // Note: These are typical RVFI signals; actual widths/names depend on
  // the Ibex version you have. Adjust as needed.
  ,
  output logic        rvfi_valid,
  output logic [63:0] rvfi_order,
  output logic [31:0] rvfi_insn,
  output logic        rvfi_trap,
  output logic        rvfi_halt,
  output logic        rvfi_intr,
  output logic [ 1:0] rvfi_mode,
  output logic [ 1:0] rvfi_ixl,
  output logic [31:0] rvfi_pc_rdata,
  output logic [31:0] rvfi_pc_wdata,
  output logic [31:0] rvfi_rs1_rdata,
  output logic [31:0] rvfi_rs2_rdata,
  output logic [31:0] rvfi_rd_wdata,
  output logic [ 4:0] rvfi_rd_addr,
  output logic [31:0] rvfi_mem_addr,
  output logic [ 3:0] rvfi_mem_rmask,
  output logic [ 3:0] rvfi_mem_wmask,
  output logic [31:0] rvfi_mem_rdata,
  output logic [31:0] rvfi_mem_wdata
`endif
);

  // --------------------------------------------------------------------------
  // Instance: Ibex
  // --------------------------------------------------------------------------
  ibex_top #(
    .RV32E            (RV32E),
    .RV32M            (RV32M),
    .RV32B            (RV32B),
    .RegFile          (RegFile),
    .ICache           (ICache),
    .ICacheECC        (ICacheECC),
    .ICacheScramble   (ICacheScramble),
    .BranchTargetALU  (BranchTargetALU),
    .WritebackStage   (WritebackStage),
    .BranchPredictor  (BranchPredictor),
    .DbgTriggerEn     (DbgTriggerEn),
    .SecureIbex       (SecureIbex),
    .PMPEnable        (PMPEnable),
    .PMPGranularity   (PMPGranularity),
    .PMPNumRegions    (PMPNumRegions),
    .MHPMCounterNum   (MHPMCounterNum),
    .MHPMCounterWidth (MHPMCounterWidth)
  ) u_ibex (
    // Clocks / resets
    .clk_i            (clk_i),
    .rst_ni           (rst_ni),

    // Test / bring-up
    .test_en_i        (test_en_i),
    .fetch_enable_i   (fetch_enable_i),
    .boot_addr_i      (boot_addr_i),
    .hart_id_i        (hart_id_i),

    // Instruction port
    .instr_req_o      (instr_req_o),
    .instr_gnt_i      (instr_gnt_i),
    .instr_rvalid_i   (instr_rvalid_i),
    .instr_addr_o     (instr_addr_o),
    .instr_rdata_i    (instr_rdata_i),
    .instr_err_i      (instr_err_i),

    // Data port
    .data_req_o       (data_req_o),
    .data_gnt_i       (data_gnt_i),
    .data_rvalid_i    (data_rvalid_i),
    .data_we_o        (data_we_o),
    .data_be_o        (data_be_o),
    .data_addr_o      (data_addr_o),
    .data_wdata_o     (data_wdata_o),
    .data_rdata_i     (data_rdata_i),
    .data_err_i       (data_err_i),

    // IRQs
    .irq_software_i   (irq_software_i),
    .irq_timer_i      (irq_timer_i),
    .irq_external_i   (irq_external_i),
    .irq_fast_i       (irq_fast_i),
    .irq_nm_i         (irq_nm_i),

    // Debug
    .debug_req_i      (debug_req_i),

    // Status
    .core_sleep_o     (core_sleep_o)

`ifdef RVFI
    // RVFI (if enabled in your build)
    , .rvfi_valid       (rvfi_valid),
      .rvfi_order       (rvfi_order),
      .rvfi_insn        (rvfi_insn),
      .rvfi_trap        (rvfi_trap),
      .rvfi_halt        (rvfi_halt),
      .rvfi_intr        (rvfi_intr),
      .rvfi_mode        (rvfi_mode),
      .rvfi_ixl         (rvfi_ixl),
      .rvfi_pc_rdata    (rvfi_pc_rdata),
      .rvfi_pc_wdata    (rvfi_pc_wdata),
      .rvfi_rs1_rdata   (rvfi_rs1_rdata),
      .rvfi_rs2_rdata   (rvfi_rs2_rdata),
      .rvfi_rd_wdata    (rvfi_rd_wdata),
      .rvfi_rd_addr     (rvfi_rd_addr),
      .rvfi_mem_addr    (rvfi_mem_addr),
      .rvfi_mem_rmask   (rvfi_mem_rmask),
      .rvfi_mem_wmask   (rvfi_mem_wmask),
      .rvfi_mem_rdata   (rvfi_mem_rdata),
      .rvfi_mem_wdata   (rvfi_mem_wdata)
`endif
  );

endmodule
