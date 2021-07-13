import numpy as np
import torch
import torch.nn.functional as F
import torch.nn.intrinsic.qat as nniqat
from pycls.core.config import cfg
from torch.nn.qat import Conv2d, Linear
from torch.quantization.quantize import register_activation_post_process_hook


class ShiftScaleQuant(torch.autograd.Function):
    @staticmethod
    def forward(ctx, s):
        return torch.exp2(torch.log2(s).round())

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output


class QConvBn2d(nniqat.ConvBn2d):
    def fuse_module(self):
        from torch.quantization import fuse_conv_bn

        self.eval()
        fused_conv = fuse_conv_bn(self, self.bn)
        qconv = QConv2d(
            fused_conv.in_channels,
            fused_conv.out_channels,
            fused_conv.kernel_size,
            fused_conv.stride,
            fused_conv.padding,
            fused_conv.dilation,
            fused_conv.groups,
            True,
            fused_conv.padding_mode,
            fused_conv.qconfig,
            self.weight.device,
        )
        qconv.weight.data = fused_conv.weight.data
        qconv.bias.data = fused_conv.bias.data
        qconv.quant_bias = True
        qconv.activation_post_process = self.activation_post_process
        register_activation_post_process_hook(qconv)
        del qconv.weight_fake_quant
        qconv.weight_fake_quant = self.weight_fake_quant
        return qconv


class QConv2d(Conv2d):
    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size,
        stride=1,
        padding=0,
        dilation=1,
        groups=1,
        bias=True,
        padding_mode="zeros",
        qconfig=None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            bias=bias,
            padding_mode=padding_mode,
            qconfig=qconfig,
            device=device,
            dtype=dtype,
        )
        self.quant_bias = False

    def set_quant_bias(self, quant_bias):
        self.quant_bias = quant_bias

    def forward(self, input):
        qweight = self.weight_fake_quant(self.weight)
        qbias = self.bias
        if self.bias is not None and self.quant_bias:
            scale = ShiftScaleQuant.apply(
                F.softplus(self.activation_post_process.scale)
            )
            qbias = torch._fake_quantize_learnable_per_tensor_affine(
                self.bias,
                scale,
                torch.tensor([0.0], device=self.bias.device),
                -int(np.exp2(cfg.QUANTIZATION.QAT.ACT_BITWIDTH - 1)),
                int(np.exp2(cfg.QUANTIZATION.QAT.ACT_BITWIDTH - 1) - 1),
                1.0,
            )
        return self._conv_forward(input, qweight, qbias)


class QLinear(Linear):
    def __init__(
        self,
        in_features,
        out_features,
        bias=True,
        qconfig=None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__(
            in_features,
            out_features,
            bias=bias,
            qconfig=qconfig,
            device=device,
            dtype=dtype,
        )
        self.quant_bias = False

    def set_quant_bias(self, quant_bias):
        self.quant_bias = quant_bias

    def forward(self, input):
        qweight = self.weight_fake_quant(self.weight)
        qbias = self.bias
        if self.bias is not None and self.quant_bias:
            scale = ShiftScaleQuant.apply(
                F.softplus(self.activation_post_process.scale)
            )
            qbias = torch._fake_quantize_learnable_per_tensor_affine(
                self.bias,
                scale,
                torch.tensor([0.0], device=self.bias.device),
                -int(np.exp2(cfg.QUANTIZATION.QAT.ACT_BITWIDTH - 1)),
                int(np.exp2(cfg.QUANTIZATION.QAT.ACT_BITWIDTH - 1) - 1),
                1.0,
            )
        return F.linear(input, qweight, qbias)
