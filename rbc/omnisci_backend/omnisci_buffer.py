"""Implement Buffer type as a base class to Omnisci Array and Column types.

Omnisci Buffer represents the following structure:

  template<typename T>
  struct Buffer {
    T* ptr;
    size_t sz;
    ...
  }

that is, a structure that has at least two members where the first is
a pointer to some data type and the second is the size of the buffer.

This module implements the support for the following Python operators:

  len
  __getitem__
  __setitem__

to provide a minimal functionality for accessing and manipulating the
Omnisci buffer objects from UDF/UDTFs.
"""


import operator
from collections import defaultdict
from llvmlite import ir
import numpy as np
from rbc import typesystem
from rbc.utils import get_version
from rbc.targetinfo import TargetInfo
from llvmlite import ir as llvm_ir
if get_version('numba') >= (0, 49):
    from numba.core import datamodel, cgutils, extending, types
else:
    from numba import datamodel, cgutils, extending, types


int8_t = ir.IntType(8)
int32_t = ir.IntType(32)
int64_t = ir.IntType(64)
void_t = ir.VoidType()
fp32 = ir.FloatType()
fp64 = ir.DoubleType()


class OmnisciBufferType(typesystem.Type):
    """Typesystem type class for Omnisci buffer structures.
    """
    # When True, buffer type arguments are passed by value to
    # functions [not recommended].
    pass_by_value = False

    @classmethod
    def preprocess_args(cls, args):
        assert len(args) == 1, args
        assert len(args[0]) == 1, args
        element_type = args[0][0]
        if not isinstance(element_type, typesystem.Type):
            element_type = typesystem.Type.fromobject(element_type)
        return ((element_type,),)

    @property
    def element_type(self):
        return self[0][0]

    @property
    def buffer_extra_members(self):
        return ()

    def tonumba(self, bool_is_int8=None):
        ptr_t = typesystem.Type(self.element_type, '*', name='ptr')
        size_t = typesystem.Type.fromstring('size_t sz')
        extra_members = tuple(map(typesystem.Type.fromobject, self.buffer_extra_members))
        buffer_type = typesystem.Type(
            ptr_t,
            size_t,
            *extra_members
        )
        buffer_type._params['numba.Type'] = BufferType
        numba_type = buffer_type.tonumba(bool_is_int8=True)
        if self.pass_by_value:
            return numba_type
        numba_eltype = self.element_type.tonumba(bool_is_int8=True)
        numba_type_ptr = BufferPointer(numba_type, numba_eltype)
        return numba_type_ptr


class BufferType(types.Type):
    """Numba type class for Omnisci buffer structures.
    """

    @property
    def eltype(self):
        """
        Return buffer element dtype.
        """
        return self.members[0].dtype


class BufferPointer(types.Type):
    """Numba type class for pointers to Omnisci buffer structures.

    We are not deriving from CPointer because BufferPointer getitem is
    used to access the data stored in Buffer ptr member.
    """
    mutable = True
    return_as_first_argument = True

    def __init__(self, dtype, eltype):
        self.dtype = dtype    # struct dtype
        self.eltype = eltype  # buffer element dtype
        name = "(%s)*" % dtype
        super().__init__(name)

    @property
    def key(self):
        return self.dtype


class BufferMeta(type):

    class_names = set()

    def __init__(cls, name, bases, dct):
        type(cls).class_names.add(name)


class Buffer(object, metaclass=BufferMeta):
    """Represents Omnisci Buffer that can be constructed within UDF/UDTFs.
    """


@datamodel.register_default(BufferPointer)
class BufferPointerModel(datamodel.models.PointerModel):
    """Base class for Omnisci buffer pointer models.

    Subclasses should register the model for the corresponding
    BufferPointer subclass, for instance::

      @datamodel.register_default(ArrayPointer)
      class ArrayPointerModel(BufferPointerModel):
          pass
    """


builder_buffers = defaultdict(list)


def omnisci_buffer_constructor(context, builder, sig, args):
    """

    Usage:

      extending.lower_builtin(MyBuffer, numba.types.Integer, ...)(omnisci_buffer_constructor)

    will enable creating MyBuffer instance from a Omnisci UDF/UDTF definition:

      b = MyBuffer(<size>, ...)

    """
    ptr_type, sz_type = sig.return_type.dtype.members[:2]
    if len(sig.return_type.dtype.members) > 2:
        assert len(sig.return_type.dtype.members) == 3
        null_type = sig.return_type.dtype.members[2]
    else:
        null_type = None
    assert isinstance(args[0].type, ir.IntType), (args[0].type)
    element_count = builder.zext(args[0], int64_t)
    element_size = int64_t(ptr_type.dtype.bitwidth // 8)

    alloc_fnty = ir.FunctionType(int8_t.as_pointer(), [int64_t, int64_t])

    alloc_fn = builder.module.get_or_insert_function(
        alloc_fnty, name="allocate_varlen_buffer")
    ptr8 = builder.call(alloc_fn, [element_count, element_size])
    # remember possible temporary allocations so that when leaving a
    # UDF/UDTF, these will be deallocated, see omnisci_pipeline.py.
    builder_buffers[builder].append(ptr8)
    ptr = builder.bitcast(ptr8, context.get_value_type(ptr_type))

    fa = cgutils.create_struct_proxy(sig.return_type.dtype)(context, builder)
    fa.ptr = ptr                  # T*
    fa.sz = element_count         # size_t
    if null_type is not None:
        is_zero = builder.icmp_signed('==', element_count, int64_t(0))
        with builder.if_else(is_zero) as (then, orelse):
            with then:
                is_null = context.get_value_type(null_type)(1)
            with orelse:
                is_null = context.get_value_type(null_type)(0)
        fa.is_null = is_null      # int8_t
    return fa._getpointer()


@extending.intrinsic
def free_omnisci_buffer(typingctx, ret):
    sig = types.void(ret)

    def codegen(context, builder, signature, args):
        buffers = builder_buffers[builder]

        # TODO: using stdlib `free` that works only for CPU. For CUDA
        # devices, we need to use omniscidb provided deallocator.
        free_fnty = llvm_ir.FunctionType(void_t, [int8_t.as_pointer()])
        free_fn = builder.module.get_or_insert_function(free_fnty, name="free")

        # We skip the ret pointer iff we're returning a Buffer
        # otherwise, we free everything
        if isinstance(ret, BufferPointer):
            [arg] = args
            ptr = builder.load(builder.gep(arg, [int32_t(0), int32_t(0)]))
            ptr = builder.bitcast(ptr, int8_t.as_pointer())
            for ptr8 in buffers:
                with builder.if_then(builder.icmp_signed('!=', ptr, ptr8)):
                    builder.call(free_fn, [ptr8])
        else:
            for ptr8 in buffers:
                builder.call(free_fn, [ptr8])

        del builder_buffers[builder]

    return sig, codegen


@extending.intrinsic
def omnisci_buffer_ptr_len_(typingctx, data):
    sig = types.int64(data)

    def codegen(context, builder, signature, args):
        data, = args
        rawptr = cgutils.alloca_once_value(builder, value=data)
        struct = builder.load(builder.gep(rawptr,
                                          [int32_t(0)]))
        return builder.load(builder.gep(
            struct, [int32_t(0), int32_t(1)]))
    return sig, codegen


@extending.intrinsic
def omnisci_buffer_len_(typingctx, data):
    sig = types.int64(data)

    def codegen(context, builder, signature, args):
        data, = args
        assert data.opname == 'load'
        struct = data.operands[0]
        return builder.load(builder.gep(
            struct, [int32_t(0), int32_t(1)]))

    return sig, codegen


@extending.overload(len)
def omnisci_buffer_len(x):
    if isinstance(x, BufferPointer):
        return lambda x: omnisci_buffer_ptr_len_(x)
    if isinstance(x, BufferType):
        return lambda x: omnisci_buffer_len_(x)


@extending.intrinsic
def omnisci_buffer_ptr_getitem_(typingctx, data, index):
    sig = data.eltype(data, index)

    def codegen(context, builder, signature, args):
        data, index = args
        rawptr = cgutils.alloca_once_value(builder, value=data)
        buf = builder.load(builder.gep(rawptr, [int32_t(0)]))
        ptr = builder.load(builder.gep(
            buf, [int32_t(0), int32_t(0)]))
        res = builder.load(builder.gep(ptr, [index]))

        return res
    return sig, codegen


@extending.intrinsic
def omnisci_buffer_getitem_(typingctx, data, index):
    eltype = data.eltype
    sig = eltype(data, index)

    def codegen(context, builder, signature, args):
        data, index = args
        assert data.opname == 'load'
        buf = data.operands[0]

        ptr = builder.load(builder.gep(
            buf, [int32_t(0), int32_t(0)]))
        res = builder.load(builder.gep(ptr, [index]))

        return res
    return sig, codegen


@extending.overload(operator.getitem)
def omnisci_buffer_getitem(x, i):
    if isinstance(x, BufferPointer):
        return lambda x, i: omnisci_buffer_ptr_getitem_(x, i)
    if isinstance(x, BufferType):
        return lambda x, i: omnisci_buffer_getitem_(x, i)


# [rbc issue-197] Numba promotes operations like
# int32(a) + int32(b) to int64
def truncate_or_extend(builder, nb_value, eltype, value, buf_typ):
    # buf[pos] = val

    if isinstance(nb_value, types.Integer):  # Integer
        if eltype.bitwidth < nb_value.bitwidth:
            return builder.trunc(value, buf_typ)  # truncate
        elif eltype.bitwidth > nb_value.bitwidth:
            is_signed = nb_value.signed
            return builder.sext(value, buf_typ) if is_signed else \
                builder.zext(value, buf_typ)  # extend
    elif isinstance(nb_value, types.Float):  # Floating-point
        if eltype.bitwidth < nb_value.bitwidth:
            return builder.fptrunc(value, buf_typ)  # truncate
        elif eltype.bitwidth > nb_value.bitwidth:
            return builder.fpext(value, buf_typ)  # extend
    elif isinstance(nb_value, types.Boolean):
        if buf_typ.width < value.type.width:
            return builder.trunc(value, buf_typ)
        elif buf_typ.width > value.type.width:
            return builder.zext(value, buf_typ)

    return value


@extending.intrinsic
def omnisci_buffer_ptr_setitem_(typingctx, data, index, value):
    sig = types.none(data, index, value)

    eltype = data.eltype
    nb_value = value

    def codegen(context, builder, signature, args):
        zero = int32_t(0)

        data, index, value = args

        rawptr = cgutils.alloca_once_value(builder, value=data)
        ptr = builder.load(rawptr)

        buf = builder.load(builder.gep(ptr, [zero, zero]))
        value = truncate_or_extend(builder, nb_value, eltype, value, buf.type.pointee)
        builder.store(value, builder.gep(buf, [index]))

    return sig, codegen


@extending.intrinsic
def omnisci_buffer_setitem_(typingctx, data, index, value):
    sig = types.none(data, index, value)

    eltype = data.members[0].dtype
    nb_value = value

    def codegen(context, builder, signature, args):
        zero = int32_t(0)

        data, index, value = args

        assert data.opname == 'load'
        buf = data.operands[0]

        ptr = builder.load(builder.gep(buf, [zero, zero]))
        value = truncate_or_extend(builder, nb_value, eltype, value, ptr.type.pointee)

        builder.store(value, builder.gep(ptr, [index]))

    return sig, codegen


@extending.overload(operator.setitem)
def omnisci_buffer_setitem(a, i, v):
    if isinstance(a, BufferPointer):
        return lambda a, i, v: omnisci_buffer_ptr_setitem_(a, i, v)
    if isinstance(a, BufferType):
        return lambda a, i, v: omnisci_buffer_setitem_(a, i, v)


@extending.intrinsic
def omnisci_buffer_is_null_(typingctx, data):
    sig = types.int8(data)

    def codegen(context, builder, sig, args):
        rawptr = cgutils.alloca_once_value(builder, value=args[0])
        ptr = builder.load(rawptr)
        return builder.load(builder.gep(ptr, [int32_t(0), int32_t(2)]))

    return sig, codegen


@extending.intrinsic
def omnisci_buffer_set_null_(typingctx, data):
    sig = types.none(data)

    def codegen(context, builder, sig, args):
        rawptr = cgutils.alloca_once_value(builder, value=args[0])
        ptr = builder.load(rawptr)
        builder.store(int8_t(1), builder.gep(ptr, [int32_t(0), int32_t(2)]))

    return sig, codegen


@extending.intrinsic
def omnisci_array_is_null_(typingctx, T, elem):
    sig = types.boolean(T, elem)

    target_info = TargetInfo()
    null_value = target_info.null_values[f'Array<{T.dtype}>']
    # The server sends numbers as unsigned values rather than signed ones.
    # Thus, 129 should be read as -127 (overflow). See rbc issue #254
    if str(T.dtype) not in ['float32', 'float64']:
        null_value = np.uint64(null_value).astype(str(T.dtype))
    nv = ir.Constant(ir.IntType(T.dtype.bitwidth), null_value)

    def codegen(context, builder, signature, args):
        _, elem = args
        if isinstance(T.dtype, types.Float):
            elem = builder.bitcast(elem, nv.type)
        return builder.icmp_signed('==', elem, nv)

    return sig, codegen


# "BufferPointer.is_null" checks if a given array or column is null
# as opposed to "BufferType.is_null" that checks if an index in a
# column is null
@extending.overload_method(BufferPointer, 'is_null')
def omnisci_buffer_is_null(x, row_idx=None):
    T = x.eltype
    if isinstance(x, BufferPointer):
        if cgutils.is_nonelike(row_idx):
            def impl(x, row_idx=None):
                return omnisci_buffer_is_null_(x)
        else:
            def impl(x, row_idx=None):
                return omnisci_array_is_null_(T, x[row_idx])
        return impl


@extending.intrinsic
def omnisci_array_set_null_(typingctx, arr, row_idx):
    T = arr.eltype
    sig = types.none(arr, row_idx)

    target_info = TargetInfo()
    null_value = target_info.null_values[f'Array<{T}>']

    # The server sends numbers as unsigned values rather than signed ones.
    # Thus, 129 should be read as -127 (overflow). See rbc issue #129
    if str(T) not in ['float32', 'float64']:
        null_value = np.uint64(null_value).astype(str(T))

    def codegen(context, builder, signature, args):
        # get the operator.setitem intrinsic
        fnop = context.typing_context.resolve_value_type(omnisci_buffer_ptr_setitem_)
        setitem_sig = types.none(arr, row_idx, T)
        # register the intrinsic in the typing ctx
        fnop.get_call_type(context, setitem_sig.args, {})
        intrinsic = context.get_function(fnop, setitem_sig)

        data, index = args
        # data = {T*, i64, i8}*
        ty = data.type.pointee.elements[0].pointee
        nv = ir.Constant(ir.IntType(T.bitwidth), null_value)
        if isinstance(T, types.Float):
            nv = builder.bitcast(nv, ty)
        intrinsic(builder, (data, index, nv,))

    return sig, codegen


@extending.overload_method(BufferPointer, 'set_null')
def omnisci_buffer_set_null(x, row_idx=None):
    if isinstance(x, BufferPointer):
        if cgutils.is_nonelike(row_idx):
            def impl(x, row_idx=None):
                return omnisci_buffer_set_null_(x)
        else:
            def impl(x, row_idx=None):
                return omnisci_array_set_null_(x, row_idx)
            return impl
        return impl
