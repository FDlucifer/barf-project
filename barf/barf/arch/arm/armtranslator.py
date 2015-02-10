import logging

import barf

from barf.arch import ARCH_ARM_MODE_32
from barf.arch import ARCH_ARM_MODE_64
from barf.arch.arm.armbase import ArmArchitectureInformation
from barf.arch.arm.armbase import ArmShifterOperand
from barf.arch.arm.armbase import ArmImmediateOperand
from barf.arch.arm.armbase import ArmMemoryOperand
from barf.arch.arm.armbase import ArmRegisterOperand
from barf.arch.arm.armbase import ArmRegisterListOperand
from barf.core.reil import ReilEmptyOperand
from barf.core.reil import ReilImmediateOperand
from barf.core.reil import ReilInstructionBuilder
from barf.core.reil import ReilInstruction
from barf.core.reil import ReilMnemonic
from barf.core.reil import ReilRegisterOperand
from barf.utils.utils import VariableNamer
from barf.arch.arm.armbase import ARM_MEMORY_INDEX_OFFSET
from barf.arch.arm.armbase import ARM_MEMORY_INDEX_POST
from barf.arch.arm.armbase import ARM_MEMORY_INDEX_PRE
from barf.arch.arm.armbase import ARM_COND_CODE_EQ
from barf.arch.arm.armbase import ARM_COND_CODE_NE
from barf.arch.arm.armbase import ARM_COND_CODE_CS
from barf.arch.arm.armbase import ARM_COND_CODE_CC
from barf.arch.arm.armbase import ARM_COND_CODE_MI
from barf.arch.arm.armbase import ARM_COND_CODE_PL
from barf.arch.arm.armbase import ARM_COND_CODE_VS
from barf.arch.arm.armbase import ARM_COND_CODE_VC
from barf.arch.arm.armbase import ARM_COND_CODE_HI
from barf.arch.arm.armbase import ARM_COND_CODE_LS
from barf.arch.arm.armbase import ARM_COND_CODE_GE
from barf.arch.arm.armbase import ARM_COND_CODE_LT
from barf.arch.arm.armbase import ARM_COND_CODE_GT
from barf.arch.arm.armbase import ARM_COND_CODE_LE
from barf.arch.arm.armbase import ARM_COND_CODE_AL
from barf.arch.arm.armbase import ARM_LDM_STM_IA
from barf.arch.arm.armbase import ARM_LDM_STM_IB
from barf.arch.arm.armbase import ARM_LDM_STM_DA
from barf.arch.arm.armbase import ARM_LDM_STM_DB
from barf.arch.arm.armbase import ARM_LDM_STM_FD
from barf.arch.arm.armbase import ldm_stack_am_to_non_stack_am
from barf.arch.arm.armbase import stm_stack_am_to_non_stack_am
FULL_TRANSLATION = 0
LITE_TRANSLATION = 1

logger = logging.getLogger(__name__)

class Label(object):

    def __init__(self, name):
        self.name = name

    def __str__(self):
        string = self.name + ":"

        return string

class TranslationBuilder(object):

    def __init__(self, ir_name_generator, architecture_mode):
        self._ir_name_generator = ir_name_generator

        self._arch_info = ArmArchitectureInformation(architecture_mode)

        self._instructions = []

        self._builder = ReilInstructionBuilder()
        
    def add(self, instr):
        self._instructions.append(instr)

    def temporal(self, size):
        return ReilRegisterOperand(self._ir_name_generator.get_next(), size)

    def immediate(self, value, size):
        return ReilImmediateOperand(value, size)

    def label(self, name):
        return Label(name)

    def instanciate(self, address):
        # Set instructions address.
        instrs = self._instructions

        for instr in instrs:
            instr.address = address << 8
            
        instrs = self._resolve_loops(instrs)

        return instrs

    def read(self, arm_operand):

        if isinstance(arm_operand, ArmImmediateOperand):

            reil_operand = ReilImmediateOperand(arm_operand.immediate, arm_operand.size)

        elif isinstance(arm_operand, ArmRegisterOperand):

            reil_operand = ReilRegisterOperand(arm_operand.name, arm_operand.size)

        elif isinstance(arm_operand, ArmShifterOperand):
            
            reil_operand = self._compute_shifter_operand(arm_operand)

        elif isinstance(arm_operand, ArmMemoryOperand):
 
            addr = self._compute_memory_address(arm_operand)
 
            reil_operand = self.temporal(arm_operand.size)
 
            self.add(self._builder.gen_ldm(addr, reil_operand))
            
        elif isinstance(arm_operand, ArmRegisterListOperand):
 
            reil_operand = self._compute_register_list(arm_operand)
 
        else:
            raise NotImplementedError("Instruction Not Implemented: Unknown operand for read operation.")

        return reil_operand

    def write(self, arm_operand, value):

        if isinstance(arm_operand, ArmRegisterOperand):

            reil_operand = ReilRegisterOperand(arm_operand.name, arm_operand.size)

            self.add(self._builder.gen_str(value, reil_operand))

        elif isinstance(arm_operand, ArmMemoryOperand):
 
            addr = self._compute_memory_address(arm_operand)
 
            self.add(self._builder.gen_stm(value, addr))

        else:
            raise NotImplementedError("Instruction Not Implemented: Unknown operand for write operation.")

    def _resolve_loops(self, instrs):
        idx_by_labels = {}

        # Collect labels.
#         curr = 0
#         for index, instr in enumerate(instrs):
#             if isinstance(instr, Label):
#                 idx_by_labels[instr.name] = curr
# 
#                 del instrs[index]
#             else:
#                 curr += 1


        # TODO: Hack to avoid deleting while iterating
        instrs_no_labels = []
        curr = 0
        for i in instrs:
            if isinstance(i, Label):
                idx_by_labels[i.name] = curr
            else:
                instrs_no_labels.append(i)
                curr += 1
            
        instrs[:] = instrs_no_labels



        # Resolve instruction addresses and JCC targets.
        for index, instr in enumerate(instrs):
            assert isinstance(instr, ReilInstruction)

            instr.address |= index

            if instr.mnemonic == ReilMnemonic.JCC:
                target = instr.operands[2]

                if isinstance(target, Label):
                    idx = idx_by_labels[target.name]
                    address = (instr.address & ~0xff) | idx

                    instr.operands[2] = ReilImmediateOperand(address, 40)

        return instrs

    def _compute_shifter_operand(self, sh_op):
        
        base = ReilRegisterOperand(sh_op.base_reg.name, sh_op.size)
        
        if sh_op.shift_amount:
            ret = self.temporal(sh_op.size)
            
            if isinstance(sh_op.shift_amount, ArmImmediateOperand):
                sh_am = ReilImmediateOperand(sh_op.shift_amount.immediate, sh_op.size)
            elif isinstance(sh_op.shift_amount, ArmRegisterOperand):
                sh_am = ReilRegisterOperand(sh_op.shift_amount.name, sh_op.shift_amount.size)
            else:
                raise NotImplementedError("Instruction Not Implemented: Unknown shift amount type.")
            
            if (sh_op.shift_type == 'lsl'):
                self.add(self._builder.gen_bsh(base, sh_am, ret))
            else:
                # TODO: Implement other shift types
                raise NotImplementedError("Instruction Not Implemented: Shift type.")
        else:
            ret = base

        return ret

    def _compute_memory_address(self, mem_operand):
        """Return operand memory access translation.
        """
        base = ReilRegisterOperand(mem_operand.base_reg.name, mem_operand.size)
        
        if mem_operand.displacement:
            address = self.temporal(mem_operand.size)
            
            if isinstance(mem_operand.displacement, ArmRegisterOperand):
                disp = ReilRegisterOperand(mem_operand.displacement.name, mem_operand.size)
            elif isinstance(mem_operand.displacement, ArmImmediateOperand):
                disp = ReilImmediateOperand(mem_operand.displacement.immediate, mem_operand.size)
            elif isinstance(mem_operand.displacement, ArmShifterOperand):
                disp = self._compute_shifter_operand(mem_operand.displacement)
            else:
                raise Exception("_compute_memory_address: displacement type unknown")
            
            if mem_operand.index_type == ARM_MEMORY_INDEX_PRE:
                if mem_operand.disp_minus:
                    self.add(self._builder.gen_sub(base, disp, address))
                else:
                    self.add(self._builder.gen_add(base, disp, address))
                self.add(self._builder.gen_str(address, base))
            elif mem_operand.index_type == ARM_MEMORY_INDEX_OFFSET:
                if mem_operand.disp_minus:
                    self.add(self._builder.gen_sub(base, disp, address))
                else:
                    self.add(self._builder.gen_add(base, disp, address))
            elif mem_operand.index_type == ARM_MEMORY_INDEX_POST:
                self.add(self._builder.gen_str(base, address))
                tmp = self.temporal(base.size)
                if mem_operand.disp_minus:
                    self.add(self._builder.gen_sub(base, disp, tmp))
                else:
                    self.add(self._builder.gen_add(base, disp, tmp))
                self.add(self._builder.gen_str(tmp, base))
            else:
                raise Exception("_compute_memory_address: indexing type unknown")

        else:
            address = base

        return address

    def _compute_register_list(self, operand):
        """Return operand register list.
        """
        
        ret = []
        for reg_range in operand.reg_list:
            if len(reg_range) == 1:
                ret.append(ReilRegisterOperand(reg_range[0].name, reg_range[0].size))
            else:
                reg_num = int(reg_range[0][1:]) # Assuming the register is named with one letter + number
                reg_end = int(reg_range[1][1:])
                if reg_num > reg_end:
                    raise NotImplementedError("Instruction Not Implemented: Invalid register range.")
                while reg_num <= reg_end:
                    ret.append(ReilRegisterOperand(reg_range[0].name[0] + str(reg_num), reg_range[0].size))
                    reg_num = reg_num + 1
        
        return ret
    
    def _all_ones_imm(self, reg):
        return self.immediate((2**reg.size) - 1, reg.size)

    def _negate_reg(self, reg):
        neg = self.temporal(reg.size)
        self.add(self._builder.gen_xor(reg, self._all_ones_imm(reg), neg))
        return neg
    
    def _and_regs(self, reg1, reg2):
        ret = self.temporal(reg1.size)
        self.add(self._builder.gen_and(reg1, reg2, ret))
        return ret
        
    def _or_regs(self, reg1, reg2):
        ret = self.temporal(reg1.size)
        self.add(self._builder.gen_or(reg1, reg2, ret))
        return ret
        
    def _xor_regs(self, reg1, reg2):
        ret = self.temporal(reg1.size)
        self.add(self._builder.gen_xor(reg1, reg2, ret))
        return ret
        
    def _equal_regs(self, reg1, reg2):
        return self._negate_reg(self._xor_regs(reg1, reg2))
    
    def _unequal_regs(self, reg1, reg2):
        return self._xor_regs(reg1, reg2)
    
    def _extract_bit(self, reg, bit):
        assert(bit >= 0 and bit < reg.size)
        tmp = self.temporal(reg.size)
        ret = self.temporal(1)

        self.add(self._builder.gen_bsh(reg, self.immediate(-bit, reg.size), tmp)) # shift to LSB
        self.add(self._builder.gen_and(tmp, self.immediate(1, reg.size), ret)) # filter LSB
        
        return ret

    # Same as before but the bit number is indicated by a register and it will be resolved at runtime
    def _extract_bit_with_register(self, reg, bit):
        # assert(bit >= 0 and bit < reg.size2) # It is assumed, it is not checked
        tmp = self.temporal(reg.size)
        neg_bit = self.temporal(reg.size)
        ret = self.temporal(1)

        self.add(self._builder.gen_sub(self.immediate(0, bit.size), bit, neg_bit)) # as left bit is indicated by a negative number
        self.add(self._builder.gen_bsh(reg, neg_bit, tmp)) # shift to LSB
        self.add(self._builder.gen_and(tmp, self.immediate(1, reg.size), ret)) # filter LSB
        
        return ret

    def _extract_msb(self, reg):
        return self._extract_bit(reg, reg.size - 1)
    
    def _extract_sign_bit(self, reg):
        return self._extract_msb(self, reg)
    
    def _greater_than_or_equal(self, reg1, reg2):
        assert(reg1.size == reg2.size)
        result = self.temporal(reg1.size * 2)
        
        self.add(self._builder.gen_sub(reg1, reg2, result))
        
        sign = self._extract_bit(result, reg1.size - 1)
        overflow = self._overflow_from_sub(reg1, reg2, result)
        
        return self._equal_regs(sign, overflow)
    
    def _jump_to(self, target):
        self.add(self._builder.gen_jcc(self.immediate(1, 1), target))
    
    def _jump_if_zero(self, reg, label):
        is_zero = self.temporal(1)
        self.add(self._builder.gen_bisz(reg, is_zero))
        self.add(self._builder.gen_jcc(is_zero, label))
        
    def _add_to_reg(self, reg, value):
        res = self.temporal(reg.size)
        self.add(self._builder.gen_add(reg, value, res))
        
        return res

    def _sub_to_reg(self, reg, value):
        res = self.temporal(reg.size)
        self.add(self._builder.gen_sub(reg, value, res))
        
        return res

    def _overflow_from_sub(self, oprnd0, oprnd1, result):
        op1_sign = self._extract_bit(oprnd0, oprnd0.size - 1)
        op2_sign = self._extract_bit(oprnd1, oprnd0.size - 1)
        res_sign = self._extract_bit(result, oprnd0.size - 1)
        
        return self._and_regs(self._unequal_regs(op1_sign, op2_sign), self._unequal_regs(op1_sign, res_sign))
        

class ArmTranslator(object):

    """ARM to IR Translator."""

    def __init__(self, architecture_mode=ARCH_ARM_MODE_32, translation_mode=FULL_TRANSLATION):

        # Set *Architecture Mode*. The translation of each instruction
        # into the REIL language is based on this.
        self._arch_mode = architecture_mode

        # An instance of *ArchitectureInformation*.
        self._arch_info = ArmArchitectureInformation(architecture_mode)

        # Set *Translation Mode*.
        self._translation_mode = translation_mode

        # An instance of a *VariableNamer*. This is used so all the
        # temporary REIL registers are unique.
        self._ir_name_generator = VariableNamer("t", separator="")

        self._builder = ReilInstructionBuilder()

        self._flags = {
            "nf" : ReilRegisterOperand("nf", 1),
            "zf" : ReilRegisterOperand("zf", 1),
            "cf" : ReilRegisterOperand("cf", 1),
            "vf" : ReilRegisterOperand("vf", 1),
        }

        if self._arch_mode == ARCH_ARM_MODE_32:
            self._sp = ReilRegisterOperand("sp", 32)
            self._pc = ReilRegisterOperand("pc", 32)
            self._lr = ReilRegisterOperand("lr", 32)

            self._ws = ReilImmediateOperand(4, 32) # word size
        elif self._arch_mode == ARCH_ARM_MODE_64:
            self._sp = ReilRegisterOperand("sp", 64)
            self._pc = ReilRegisterOperand("pc", 64)
            self._lr = ReilRegisterOperand("lr", 64)

            self._ws = ReilImmediateOperand(8, 64) # word size

    def translate(self, instruction):
        """Return IR representation of an instruction.
        """
        try:
            trans_instrs = self._translate(instruction)
        except NotImplementedError as e:
            trans_instrs = [self._builder.gen_unkn()]

            self._log_not_supported_instruction(instruction)
            print("NotImplementedError: " + str(e))
            print(instruction)
#         except Exception as e:
#             trans_instrs = [self._builder.gen_unkn()]
#             self._log_translation_exception(instruction)
#             print("Exception: " + str(e))
#             print(instruction)

        return trans_instrs

    def _translate(self, instruction):
        """Translate a arm instruction into REIL language.

        :param instruction: a arm instruction
        :type instruction: ArmInstruction
        """
        
        # Retrieve translation function.
        translator_name = "_translate_" + instruction.mnemonic
        translator_fn = getattr(self, translator_name, self._not_implemented)

        # Translate instruction.
        tb = TranslationBuilder(self._ir_name_generator, self._arch_mode)

        # Pre-processing: evaluate flags
        nop_cc_lbl = tb.label('condition_code_not_met')
        self._evaluate_condition_code(tb, instruction, nop_cc_lbl)
        
        
        translator_fn(tb, instruction)


        tb.add(nop_cc_lbl)
        
        return tb.instanciate(instruction.address)

    def reset(self):
        """Restart IR register name generator.
        """
        self._ir_name_generator.reset()

    @property
    def translation_mode(self):
        """Get translation mode.
        """
        return self._translation_mode

    @translation_mode.setter
    def translation_mode(self, value):
        """Set translation mode.
        """
        self._translation_mode = value

    def _log_not_supported_instruction(self, instruction):
        bytes_str = " ".join("%02x" % ord(b) for b in instruction.bytes)

        logger.info(
            "Instruction not supported: %s (%s [%s])",
            instruction.mnemonic,
            instruction,
            bytes_str
        )

    def _log_translation_exception(self, instruction):
        bytes_str = " ".join("%02x" % ord(b) for b in instruction.bytes)

        logger.error(
            "Failed to translate arm to REIL: %s (%s)",
            instruction,
            bytes_str,
            exc_info=True
        )

# ============================================================================ #

    def _not_implemented(self, tb, instruction):
        raise NotImplementedError("Instruction Not Implemented")

# Translators
# ============================================================================ #
# ============================================================================ #

# "Flags"
# ============================================================================ #
    def _update_nf(self, tb, oprnd0, oprnd1, result):
        sign = tb._extract_bit(result, oprnd0.size - 1)
        tb.add(self._builder.gen_str(sign, self._flags["nf"]))

    def _carry_from_uf(self, tb, oprnd0, oprnd1, result):
        assert (result.size == oprnd0.size * 2)
        
        carry = tb._extract_bit(result, oprnd0.size)
        tb.add(self._builder.gen_str(carry, self._flags["cf"]))
        
    def _borrow_from_uf(self, tb, oprnd0, oprnd1, result):
        # BorrowFrom as defined in the ARM Reference Manual has the same implementation as CarryFrom
        self._carry_from_uf(tb, oprnd0, oprnd1, result)
        
    def _overflow_from_add_uf(self, tb, oprnd0, oprnd1, result):
        op1_sign = tb._extract_bit(oprnd0, oprnd0.size - 1)
        op2_sign = tb._extract_bit(oprnd1, oprnd0.size - 1)
        res_sign = tb._extract_bit(result, oprnd0.size - 1)
        
        overflow =  tb._and_regs(tb._equal_regs(op1_sign, op2_sign), tb._unequal_regs(op1_sign, res_sign))
        tb.add(self._builder.gen_str(overflow, self._flags["vf"]))
        
    # Evaluate overflow and update the flag
    def _overflow_from_sub_uf(self, tb, oprnd0, oprnd1, result):
        tb.add(self._builder.gen_str(tb._overflow_from_sub(oprnd0, oprnd1, result), self._flags["vf"]))
        
    def _update_zf(self, tb, oprnd0, oprnd1, result):
        zf = self._flags["zf"]

        imm0 = tb.immediate((2**oprnd0.size)-1, result.size)

        tmp0 = tb.temporal(oprnd0.size)

        tb.add(self._builder.gen_and(result, imm0, tmp0))  # filter low part of result
        tb.add(self._builder.gen_bisz(tmp0, zf))
        
    def _shifter_carry_out(self, tb, shifter_operand, oprnd0, oprnd1, result):
        if isinstance(shifter_operand, ArmImmediateOperand):
            # Assuming rotate_imm == 0 then shifter_carry_out = C flag => C flag unchanged
            return
        elif isinstance(shifter_operand, ArmRegisterOperand):
            # shifter_carry_out = C flag => C flag unchanged
            return
        elif isinstance(shifter_operand, ArmShifterOperand):
            base = ReilRegisterOperand(shifter_operand.base_reg.name, shifter_operand.size)
            shift_type = shifter_operand.shift_type
            shift_amount = shifter_operand.shift_amount
            
            if (shift_type == 'lsl'):
                
                if isinstance(shift_amount, ArmImmediateOperand):
                    if shift_amount.immediate == 0:
                        # (shifter_carry_out = C Flag)
                        return
                    else:
                        # shifter_carry_out = Rm[32 - shift_imm]
                        shift_carry_out = tb._extract_bit(base, 32 - shift_amount.immediate)
                        
                elif isinstance(shift_amount, ArmRegisterOperand):
                    # Rs: register with shift amount
                    # if Rs[7:0] == 0 then            
                    #     shifter_carry_out = C Flag
                    # else if Rs[7:0] < 32 then
                    #     shifter_carry_out = Rm[32 - Rs[7:0]]
                    # else if Rs[7:0] == 32 then
                    #     shifter_carry_out = Rm[0]
                    # else /* Rs[7:0] > 32 */
                    #     shifter_carry_out = 0
                    
                    shift_carry_out = tb.temporal(1)
                    tb.add(self._builder.gen_str(self._flags["cf"], shift_carry_out))
                    rs = ReilRegisterOperand(shift_amount.name, shift_amount.size)
                    rs_7_0 = tb._and_regs(rs, tb.immediate(0xFF, rs.size))
                    
                    end_label = tb.label('end_label')
                    rs_greater_32_label = tb.label('rs_greater_32_label')
                    
                    # if Rs[7:0] == 0 then            
                    #     shifter_carry_out = C Flag
                    tb._jump_if_zero(rs_7_0, end_label) # shift_carry_out already has the C flag set, so do nothing
                    
                    tb.add(self._builder.gen_jcc(tb._greater_than_or_equal(rs_7_0, tb.immediate(33, rs_7_0.size)),
                                                 rs_greater_32_label))
                    
                    # Rs > 0 and Rs <= 32
                    #     shifter_carry_out = Rm[32 - Rs[7:0]]
                    extract_bit_number = tb.temporal(rs_7_0.size)
                    tb.add(self._builder.gen_sub(tb.immediate(32, rs_7_0.size), rs_7_0, extract_bit_number))
                    tb.add(self._builder.gen_str(tb._extract_bit_with_register(base, extract_bit_number),
                                                 shift_carry_out))
                    tb._jump_to(end_label)
                    
                    # else /* Rs[7:0] > 32 */
                    #     shifter_carry_out = 0
                    tb.add(rs_greater_32_label)
                    tb.add(self._builder.gen_str(tb.immediate(0, 1), shift_carry_out))
#                     tb._jump_to(end_label)
                    
                    tb.add(end_label)
                    
                else:
                    raise Exception("shifter_carry_out: Unknown shift amount type.")
                
            else:
                # TODO: Implement other shift types
                raise NotImplementedError("Instruction Not Implemented: shifter_carry_out: shift type " + shifter_operand.shift_type)
            
        else:
            raise Exception("shifter_carry_out: Unknown operand type.")

        tb.add(self._builder.gen_str(shift_carry_out, self._flags["cf"]))
    
    def _update_flags_data_proc_add(self, tb, oprnd0, oprnd1, result):
        self._update_zf(tb, oprnd0, oprnd1, result)
        self._update_nf(tb, oprnd0, oprnd1, result)
        self._carry_from_uf(tb, oprnd0, oprnd1, result)
        self._overflow_from_add_uf(tb, oprnd0, oprnd1, result)

    def _update_flags_data_proc_sub(self, tb, oprnd0, oprnd1, result):
        self._update_zf(tb, oprnd0, oprnd1, result)
        self._update_nf(tb, oprnd0, oprnd1, result)
        self._borrow_from_uf(tb, oprnd0, oprnd1, result)
        tb._overflow_from_sub(oprnd0, oprnd1, result)

    def _update_flags_data_proc_other(self, tb, shifter_operand, oprnd0, oprnd1, result):
        self._update_zf(tb, oprnd0, oprnd1, result)
        self._update_nf(tb, oprnd0, oprnd1, result)
        self._shifter_carry_out(tb, shifter_operand, oprnd0, oprnd1, result)
        # Overflow Flag (V) unaffected

    def _update_flags_other(self, tb, oprnd0, oprnd1, result):
        self._update_zf(tb, oprnd0, oprnd1, result)
        self._update_nf(tb, oprnd0, oprnd1, result)
        # Carry Flag (C) unaffected
        # Overflow Flag (V) unaffected

    def _undefine_flag(self, tb, flag):
        # NOTE: In every test I've made, each time a flag is leave
        # undefined it is always set to 0.

        imm = tb.immediate(0, flag.size)

        tb.add(self._builder.gen_str(imm, flag))

    def _clear_flag(self, tb, flag):
        imm = tb.immediate(0, flag.size)

        tb.add(self._builder.gen_str(imm, flag))

    def _set_flag(self, tb, flag):
        imm = tb.immediate(1, flag.size)

        tb.add(self._builder.gen_str(imm, flag))
        
        
    # EQ: Z set
    def _evaluate_eq(self, tb):
        return self._flags["zf"]

    # NE: Z clear
    def _evaluate_ne(self, tb):
        return tb._all_ones_imm(self._flags["zf"])
    
    # CS: C set
    def _evaluate_cs(self, tb):
        return self._flags["cf"]

    # CC: C clear
    def _evaluate_cc(self, tb):
        return tb._all_ones_imm(self._flags["cf"])
    
    # MI: N set
    def _evaluate_mi(self, tb):
        return self._flags["nf"]

    # PL: N clear
    def _evaluate_pl(self, tb):
        return tb._all_ones_imm(self._flags["nf"])
    
    # VS: V set
    def _evaluate_vs(self, tb):
        return self._flags["vf"]

    # VC: V clear
    def _evaluate_vc(self, tb):
        return tb._all_ones_imm(self._flags["vf"])
    
    # HI: C set and Z clear
    def _evaluate_hi(self, tb):
        return tb._and_regs(self._flags["cf"], tb._all_ones_imm(self._flags["zf"]))

    # LS: C clear or Z set
    def _evaluate_ls(self, tb):
        return tb._or_regs(tb._all_ones_imm(self._flags["cf"]), self._flags["zf"])
    
    # GE: N == V
    def _evaluate_ge(self, tb):
        return tb._equal_regs(self._flags["nf"], self._flags["vf"])

    # LT: N != V
    def _evaluate_lt(self, tb):
        return tb._all_ones_imm(self._evaluate_ge(tb))
    
    # GT: (Z == 0) and (N == V)
    def _evaluate_gt(self, tb):
        return tb._and_regs(tb._all_ones_imm(self._flags["zf"]), self._evaluate_ge(tb))

    # LE: (Z == 1) or (N != V)
    def _evaluate_le(self, tb):
        return tb._or_regs(self._flags["zf"], self._evaluate_lt(tb))
    
    def _evaluate_condition_code(self, tb, instruction, nop_label):
        if (instruction.condition_code == ARM_COND_CODE_AL):
            return
        
        eval_cc_fn = {
            ARM_COND_CODE_EQ : self._evaluate_eq,
            ARM_COND_CODE_NE : self._evaluate_ne,
            ARM_COND_CODE_CS : self._evaluate_cs,
            ARM_COND_CODE_CC : self._evaluate_cc,
            ARM_COND_CODE_MI : self._evaluate_mi,
            ARM_COND_CODE_PL : self._evaluate_pl,
            ARM_COND_CODE_VS : self._evaluate_vs,
            ARM_COND_CODE_VC : self._evaluate_vc,
            ARM_COND_CODE_HI : self._evaluate_hi,
            ARM_COND_CODE_LS : self._evaluate_ls,
            ARM_COND_CODE_GE : self._evaluate_ge,
            ARM_COND_CODE_LT : self._evaluate_lt,
            ARM_COND_CODE_GT : self._evaluate_gt,
            ARM_COND_CODE_LE : self._evaluate_le,
        }
        
        neg_cond = tb._all_ones_imm(eval_cc_fn[instruction.condition_code](tb))
        
        tb.add(self._builder.gen_jcc(neg_cond, nop_label))
        
        return 
    

# "Data Transfer Instructions"
# ============================================================================ #
    def _translate_mov(self, tb, instruction):
        
        oprnd1 = tb.read(instruction.operands[1])

        tb.write(instruction.operands[0], oprnd1)
        
        if instruction.update_flags:
            self._update_flags_data_proc_other(tb, instruction.operands[1], oprnd1, None, oprnd1)

    def _translate_and(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[1])
        oprnd2 = tb.read(instruction.operands[2])

        result = tb.temporal(oprnd1.size)

        tb.add(self._builder.gen_and(oprnd1, oprnd2, result))

        tb.write(instruction.operands[0], result)
        
        if instruction.update_flags:
            self._update_flags_data_proc_other(tb, instruction.operands[2], oprnd1, oprnd2, result)

    def _translate_orr(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[1])
        oprnd2 = tb.read(instruction.operands[2])

        result = tb.temporal(oprnd1.size)

        tb.add(self._builder.gen_or(oprnd1, oprnd2, result))

        tb.write(instruction.operands[0], result)
        
        if instruction.update_flags:
            self._update_flags_data_proc_other(tb, instruction.operands[2], oprnd1, oprnd2, result)

    def _translate_eor(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[1])
        oprnd2 = tb.read(instruction.operands[2])

        result = tb.temporal(oprnd1.size)

        tb.add(self._builder.gen_xor(oprnd1, oprnd2, result))

        tb.write(instruction.operands[0], result)
        
        if instruction.update_flags:
            self._update_flags_data_proc_other(tb, instruction.operands[2], oprnd1, oprnd2, result)

    def _translate_ldr(self, tb, instruction):
        
        oprnd1 = tb.read(instruction.operands[1])

        tb.write(instruction.operands[0], oprnd1)
    
    def _translate_str(self, tb, instruction):
        
        oprnd0 = tb.read(instruction.operands[0])

        tb.write(instruction.operands[1], oprnd0)
        
    def _translate_add(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[1])
        oprnd2 = tb.read(instruction.operands[2])

        result = tb.temporal(oprnd1.size * 2)

        tb.add(self._builder.gen_add(oprnd1, oprnd2, result))

        tb.write(instruction.operands[0], result)
        
        if instruction.update_flags:
            self._update_flags_data_proc_add(tb, oprnd1, oprnd2, result)

    def _translate_sub(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[1])
        oprnd2 = tb.read(instruction.operands[2])

        result = tb.temporal(oprnd1.size * 2)

        tb.add(self._builder.gen_sub(oprnd1, oprnd2, result))

        tb.write(instruction.operands[0], result)

        if instruction.update_flags:
            self._update_flags_data_proc_sub(tb, oprnd1, oprnd2, result)

    def _translate_cmn(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[0])
        oprnd2 = tb.read(instruction.operands[1])

        result = tb.temporal(oprnd1.size * 2)

        tb.add(self._builder.gen_add(oprnd1, oprnd2, result))

        self._update_flags_data_proc_add(tb, oprnd1, oprnd2, result) # S = 1 (implied in the instruction)

    def _translate_cmp(self, tb, instruction):
        oprnd1 = tb.read(instruction.operands[0])
        oprnd2 = tb.read(instruction.operands[1])

        result = tb.temporal(oprnd1.size * 2)

        tb.add(self._builder.gen_sub(oprnd1, oprnd2, result))

        self._update_flags_data_proc_sub(tb, oprnd1, oprnd2, result) # S = 1 (implied in the instruction)

    def _translate_ldm(self, tb, instruction):
        self._translate_ldm_stm(tb, instruction, True)
    
    def _translate_stm(self, tb, instruction):
        self._translate_ldm_stm(tb, instruction, False)
    
    # TODO: RESPECT REGISTER ORDER
    # LDM and STM have exactly the same logic except one loads and the other stores
    def _translate_ldm_stm(self, tb, instruction, load = True):
        base = tb.read(instruction.operands[0])
        reg_list = tb.read(instruction.operands[1])
        
        if load:
            load_store_fn = self._builder.gen_ldm
            # Convert stack addressing modes to non-stack addressing modes
            if instruction.ldm_stm_addr_mode in ldm_stack_am_to_non_stack_am:
                instruction.ldm_stm_addr_mode = ldm_stack_am_to_non_stack_am[instruction.ldm_stm_addr_mode]
        else: # Store
            load_store_fn = self._builder.gen_stm
            if instruction.ldm_stm_addr_mode in stm_stack_am_to_non_stack_am:
                instruction.ldm_stm_addr_mode = stm_stack_am_to_non_stack_am[instruction.ldm_stm_addr_mode]

        pointer = tb.temporal(base.size)
        tb.add(self._builder.gen_str(base, pointer))
        reg_list_size_bytes = ReilImmediateOperand(self._ws.immediate * len(reg_list), base.size)
        
        if instruction.ldm_stm_addr_mode == ARM_LDM_STM_IA:
            for reg in reg_list:
                tb.add(load_store_fn(pointer, reg))
                pointer = tb._add_to_reg(pointer, self._ws)
        elif  instruction.ldm_stm_addr_mode == ARM_LDM_STM_IB:
            for reg in reg_list:
                pointer = tb._add_to_reg(pointer, self._ws)
                tb.add(load_store_fn(pointer, reg))
        elif  instruction.ldm_stm_addr_mode == ARM_LDM_STM_DA:
            reg_list.reverse() # Assuming the registry list was in increasing registry number
            for reg in reg_list:
                tb.add(load_store_fn(pointer, reg))
                pointer = tb._sub_to_reg(pointer, self._ws)
        elif  instruction.ldm_stm_addr_mode == ARM_LDM_STM_DB:
            reg_list.reverse()
            for reg in reg_list:
                pointer = tb._sub_to_reg(pointer, self._ws)
                tb.add(load_store_fn(pointer, reg))
        else:
                raise Exception("Unknown addressing mode.")
        
        # Write-back
        if instruction.operands[0].wb:
            if instruction.ldm_stm_addr_mode == ARM_LDM_STM_IA or instruction.ldm_stm_addr_mode == ARM_LDM_STM_IB:
                tmp = tb._add_to_reg(base, reg_list_size_bytes)
            elif instruction.ldm_stm_addr_mode == ARM_LDM_STM_DA or instruction.ldm_stm_addr_mode == ARM_LDM_STM_DB:
                tmp = tb._sub_to_reg(base, reg_list_size_bytes)
            tb.add(self._builder.gen_str(tmp, base))

    # PUSH and POP are equivalent to STM and LDM in FD mode with the SP (and write-back)
    # Instructions are modified to adapt it to the LDM/STM interface
    def _translate_push_pop(self, tb, instruction, translate_fn):
        sp_name = "sp"
        sp_size = instruction.operands[0].reg_list[0][0].size # Infer it from the registers list
        sp_reg = ArmRegisterOperand(sp_name, sp_size)
        sp_reg.wb = True
        instruction.operands = [sp_reg, instruction.operands[0]]
        instruction.ldm_stm_addr_mode = ARM_LDM_STM_FD
        translate_fn(tb, instruction)

    def _translate_push(self, tb, instruction):
        self._translate_push_pop(tb, instruction, self._translate_stm)

    def _translate_pop(self, tb, instruction):
        self._translate_push_pop(tb, instruction, self._translate_ldm)

    def _translate_b(self, tb, instruction):
        self._translate_branch(tb, instruction, link = False)
    
    def _translate_bl(self, tb, instruction):
        self._translate_branch(tb, instruction, link = True)
    
    def _translate_branch(self, tb, instruction, link):
        target = tb.read(instruction.operands[0])
        target = ReilImmediateOperand(target.immediate << 8, target.size + 8)
            
        if (link):
            tb.add(self._builder.gen_add(self._pc, self._ws, self._lr))
        tb._jump_to(target)
