import dgl
import math
import torch as th
import torch.nn as nn
from torch.nn import init
import dgl.function as fn
import dgl.nn.pytorch as dglnn
import torch.nn.functional as F
import numpy as np
from utils import get_activation, to_etype_name
from torch.nn.parameter import Parameter

th.set_printoptions(profile="full")

def logistic_similarity_transform(sim_ij, c, d):
    return 1 / (1 + th.exp(c * sim_ij + d))
class GCN(nn.Module):
    def __init__(self, features, nhid, nhid2, dropout):
        super(GCN, self).__init__()
        self.gc1 = GraphConvolution(features, nhid)
        self.gc2 = GraphConvolution(nhid, nhid2)

        self.dropout = dropout

    def forward(self, x, adj):
        x = F.relu(self.gc1(x, adj))
        x = F.dropout(x, self.dropout, training=self.training)
        x = self.gc2(x, adj)
        return x

class GAT(nn.Module):
    def __init__(self, features, nhid, nhid2, dropout):
        super(GAT, self).__init__()
        self.ga1 = GraphAttentionLayer(features, nhid)
        self.ga2 = GraphAttentionLayer(nhid, nhid2)

        self.dropout = dropout

    def forward(self, x, adj):
        x = F.relu(self.ga1(x, adj))
        x = F.dropout(x, self.dropout, training=self.training)
        x = self.ga2(x, adj)
        return x


class FGCN(nn.Module):
    def __init__(self, fdim_drug, fdim_disease, nhid1, nhid2, dropout):
        super(FGCN, self).__init__()
        self.FGCN1 = GCN(fdim_drug, nhid1, nhid2, dropout)
        self.FGCN2 = GCN(fdim_disease, nhid1, nhid2, dropout)

        self.dropout = dropout

    def forward(self, drug_graph, drug_sim_feat, dis_graph, disease_sim_feat):
        emb1 = self.FGCN1(drug_sim_feat, drug_graph)
        emb2 = self.FGCN2(disease_sim_feat, dis_graph)

        return emb1, emb2

class FGAT(nn.Module):
    def __init__(self, fdim_drug, fdim_disease, nhid1, nhid2, dropout):
        super(FGAT, self).__init__()
        self.FGAT1 = GAT(fdim_drug, nhid1, nhid2, dropout)
        self.FGAT2 = GAT(fdim_disease, nhid1, nhid2, dropout)

        self.dropout = dropout

    def forward(self, drug_graph, drug_sim_feat, dis_graph, disease_sim_feat):
        emb1 = self.FGAT1(drug_sim_feat, drug_graph)
        emb2 = self.FGAT2(disease_sim_feat, dis_graph)

        return emb1, emb2

class GraphAttentionLayer(nn.Module):
    """
    Simple GAT layer
    """

    def __init__(self, in_features, out_features, dropout=0.6, alpha=0.2, concat=True):
        super(GraphAttentionLayer, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha
        self.concat = concat

        self.weight = Parameter(th.empty(size=(in_features, out_features)))
        nn.init.xavier_uniform_(self.weight.data, gain=1.414)
        self.a = Parameter(th.empty(size=(2*out_features, 1)))
        nn.init.xavier_uniform_(self.a.data, gain=1.414)

        self.leakyrelu = nn.LeakyReLU(self.alpha)

    def forward(self, input, adj):
        h = th.mm(input, self.weight)
        N = h.size()[0]

        a_input = th.cat([h.repeat(1, N).view(N * N, -1), h.repeat(N, 1)], dim=1).view(N, -1, 2 * self.out_features)
        e = self.leakyrelu(th.matmul(a_input, self.a).squeeze(2))

        zero_vec = -9e15*th.ones_like(e)
        attention = th.where(adj > 0, e, zero_vec)
        attention = F.softmax(attention, dim=1)
        attention = F.dropout(attention, self.dropout, training=self.training)
        h_prime = th.matmul(attention, h)

        if self.concat:
            return F.elu(h_prime)
        else:
            return h_prime

    def __repr__(self):
        return self.__class__.__name__ + ' (' \
               + str(self.in_features) + ' -> ' \
               + str(self.out_features) + ')'

class GraphConvolution(nn.Module):
    """
    Simple GCN layer
    """

    def __init__(self, in_features, out_features, bias=True):
        super(GraphConvolution, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = Parameter(th.FloatTensor(in_features, out_features))
        if bias:
            self.bias = Parameter(th.FloatTensor(out_features))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.weight.size(1))
        self.weight.data.uniform_(-stdv, stdv)
        if self.bias is not None:
            self.bias.data.uniform_(-stdv, stdv)

    def forward(self, input, adj):
        support = th.mm(input, self.weight)
        output = th.spmm(adj, support)
        if self.bias is not None:
            return output + self.bias
        else:
            return output

    def __repr__(self):
        return self.__class__.__name__ + ' (' \
               + str(self.in_features) + ' -> ' \
               + str(self.out_features) + ')'


class Attention(nn.Module):
    def __init__(self, in_size, hidden_size=16):
        super(Attention, self).__init__()

        self.project = nn.Sequential(
            nn.Linear(in_size, hidden_size),  # in_size=75
            nn.Tanh(),
            nn.Linear(hidden_size, 1, bias=False)
        )

    def forward(self, z):
        w = self.project(z)
        beta = th.softmax(w, dim=1)
        return (beta * z).sum(1), beta


class GCMCGraphConv(nn.Module):

    def __init__(self,
                 in_feats,
                 out_feats,
                 weight=True,
                 device=None,
                 dropout_rate=0.0):
        super(GCMCGraphConv, self).__init__()
        self._in_feats = in_feats  # 909
        self._out_feats = out_feats  # 600
        self.device = device
        self.dropout = nn.Dropout(dropout_rate)

        if weight:
            self.weight = nn.Parameter(th.Tensor(in_feats, out_feats))
        else:
            self.register_parameter('weight', None)
        self.reset_parameters()

    def reset_parameters(self):
        """Reinitialize learnable parameters."""
        if self.weight is not None:
            init.xavier_uniform_(self.weight)
        # init.xavier_uniform_(self.att)

    def forward(self, graph, feat, weight=None, Two_Stage=False):
        """Compute graph convolution.

        Normalizer constant :math:`c_{ij}` is stored as two node data "ci"
        and "cj".

        Parameters
        ----------
        graph : DGLGraph
            The graph.
        feat : torch.Tensor
            The input feature
        weight : torch.Tensor, optional
            Optional external weight tensor.
        dropout : torch.nn.Dropout, optional
            Optional external dropout layer.

        Returns
        -------
        torch.Tensor
            The output feature
        """
        with graph.local_scope():
            if isinstance(feat, tuple):
                feat, _ = feat  # dst feature not used [drug or disease num , 3]
            cj = graph.srcdata['cj']
            ci = graph.dstdata['ci']
            if self.device is not None:
                cj = cj.to(self.device)
                ci = ci.to(self.device)
            if weight is not None:
                if self.weight is not None:
                    raise dgl.DGLError('External weight is provided while at the same time the'
                                       ' module has defined its own weight parameter. Please'
                                       ' create the module with flag weight=False.')
            else:
                weight = self.weight

            if weight is not None:
                feat = dot_or_identity(feat, weight, self.device)

            feat = feat * self.dropout(cj)
            graph.srcdata['h'] = feat
            graph.update_all(fn.copy_src(src='h', out='m'),
                             fn.sum(msg='m', out='h'))
            rst = graph.dstdata['h']
            rst = rst * ci

        return rst


class GCMCLayer(nn.Module):
    r"""GCMC layer

    .. math::
        z_j^{(l+1)} = \sigma_{agg}\left[\mathrm{agg}\left(
        \sum_{j\in\mathcal{N}_1}\frac{1}{c_{ij}}W_1h_j, \ldots,
        \sum_{j\in\mathcal{N}_R}\frac{1}{c_{ij}}W_Rh_j
        \right)\right]

    After that, apply an extra output projection:

    .. math::
        h_j^{(l+1)} = \sigma_{out}W_oz_j^{(l+1)}

    The equation is applied to both user nodes and movie nodes and the parameters
    are not shared unless ``share_user_item_param`` is true.

    Parameters
    ----------
    rating_vals : list of int or float
        Possible rating values.
    user_in_units : int
        Size of user input feature
    movie_in_units : int
        Size of movie input feature
    msg_units : int
        Size of message :math:`W_rh_j`
    out_units : int
        Size of of final output user and movie features
    dropout_rate : float, optional
        Dropout rate (Default: 0.0)
    agg : str, optional
        Function to aggregate messages of different ratings.
        Could be any of the supported cross type reducers:
        "sum", "max", "min", "mean", "stack".
        (Default: "stack")
    agg_act : callable, str, optional
        Activation function :math:`sigma_{agg}`. (Default: None)
    out_act : callable, str, optional
        Activation function :math:`sigma_{agg}`. (Default: None)
    share_user_item_param : bool, optional
        If true, user node and movie node share the same set of parameters.
        Require ``user_in_units`` and ``move_in_units`` to be the same.
        (Default: False)
    device: str, optional
        Which device to put data in. Useful in mix_cpu_gpu training and
        multi-gpu training
    """

    def __init__(self, rating_vals,  # [0, 1]
                 user_in_units,  # 909
                 movie_in_units,  # 909
                 msg_units,  # 1800
                 out_units,  # 75
                 dropout_rate=0.0,  # 0.3
                 agg='stack',  # 'sum'
                 agg_act=None,  # Tanh()
                 share_user_item_param=False,  # True
                 basis_units=4, device=None):  # True 4
        super(GCMCLayer, self).__init__()
        self.rating_vals = rating_vals  # [0, 1]
        self.agg = agg  # sum
        self.share_user_item_param = share_user_item_param  # True
        self.ufc = nn.Linear(msg_units, out_units)  # Linear(in_features=1800, out_features=75, bias=True)
        self.user_in_units = user_in_units  # 909
        self.msg_units = msg_units  # 1800
        if share_user_item_param:
            self.ifc = self.ufc
        else:
            self.ifc = nn.Linear(msg_units, out_units)
        if agg == 'stack':
            # divide the original msg unit size by number of rel_values to keep
            # the dimensionality
            assert msg_units % len(rating_vals) == 0
            msg_units = msg_units // len(rating_vals)

        msg_units = msg_units // 3  # 600
        self.msg_units = msg_units  # 600
        self.dropout = nn.Dropout(dropout_rate)
        self.W_r = {}
        subConv = {}
        self.basis_units = basis_units  # 4
        self.att = nn.Parameter(th.randn(len(self.rating_vals), basis_units))  # [2, 4]
        self.basis = nn.Parameter(th.randn(basis_units, user_in_units, msg_units))  # [4, 909, 600]
        for i, rating in enumerate(rating_vals):
            # PyTorch parameter name can't contain "."
            rating = to_etype_name(rating)
            rev_rating = 'rev-%s' % rating
            if share_user_item_param and user_in_units == movie_in_units:
                subConv[rating] = GCMCGraphConv(user_in_units,  # 909
                                                msg_units,  # 840
                                                weight=False,  # False
                                                device=device,
                                                dropout_rate=dropout_rate)
                subConv[rev_rating] = GCMCGraphConv(user_in_units,
                                                    msg_units,
                                                    weight=False,
                                                    device=device,
                                                    dropout_rate=dropout_rate)
            else:
                self.W_r = None
                subConv[rating] = GCMCGraphConv(user_in_units,
                                                msg_units,
                                                weight=True,
                                                device=device,
                                                dropout_rate=dropout_rate)
                subConv[rev_rating] = GCMCGraphConv(movie_in_units,
                                                    msg_units,
                                                    weight=True,
                                                    device=device,
                                                    dropout_rate=dropout_rate)
        self.conv = dglnn.HeteroGraphConv(subConv, aggregate=agg)
        self.agg_act = get_activation(agg_act)
        self.device = device
        self.reset_parameters()

    def partial_to(self, device):
        """Put parameters into device except W_r

        Parameters
        ----------
        device : torch device
            Which device the parameters are put in.
        """
        assert device == self.device
        if device is not None:
            self.ufc.cuda(device)
            if self.share_user_item_param is False:
                self.ifc.cuda(device)
            self.dropout.cuda(device)

    def reset_parameters(self):
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def forward(self, graph, drug_feat=None, dis_feat=None, Two_Stage=False):
        in_feats = {'drug': drug_feat, 'disease': dis_feat}
        mod_args = {}
        self.W = th.matmul(self.att, self.basis.view(self.basis_units, -1))
        self.W = self.W.view(-1, self.user_in_units, self.msg_units)
        for i, rating in enumerate(self.rating_vals):
            rating = to_etype_name(rating)
            rev_rating = 'rev-%s' % rating

            mod_args[rating] = (self.W[i, :, :] if self.W_r is not None else None, Two_Stage)
            mod_args[rev_rating] = (self.W[i, :, :] if self.W_r is not None else None, Two_Stage)

        out_feats = self.conv(graph, in_feats, mod_args=mod_args)
        drug_feat = out_feats['drug']
        dis_feat = out_feats['disease']

        if in_feats['disease'].shape == dis_feat.shape:
            ufeat = dis_feat.view(dis_feat.shape[0], -1)
            ifeat = drug_feat.view(drug_feat.shape[0], -1)

        drug_feat = self.agg_act(drug_feat)
        drug_feat = self.dropout(drug_feat)

        dis_feat = self.agg_act(dis_feat)
        dis_feat = self.dropout(dis_feat)

        drug_feat = self.ifc(drug_feat)
        dis_feat = self.ufc(dis_feat)

        return drug_feat, dis_feat


class GatedMultimodalLayer(nn.Module):
    """
    Gated Multimodal Layer based on 'Gated multimodal networks,
    Arevalo1 et al.' (https://arxiv.org/abs/1702.01992)
    """

    def __init__(self, size_in1, size_in2, size_out=16):
        super(GatedMultimodalLayer, self).__init__()
        self.size_in1, self.size_in2, self.size_out = size_in1, size_in2, size_out

        self.hidden1 = nn.Linear(size_in1, size_out, bias=False)
        self.hidden2 = nn.Linear(size_in2, size_out, bias=False)
        self.hidden_sigmoid = nn.Linear(size_out * 2, 1, bias=False)

        # Activation functions
        self.tanh_f = nn.ReLU()
        self.sigmoid_f = nn.Sigmoid()

    def forward(self, x1, x2):
        h1 = self.tanh_f(self.hidden1(x1))
        h2 = self.tanh_f(self.hidden1(x2))
        x = th.cat((h1, h2), dim=1)
        z = self.sigmoid_f(self.hidden_sigmoid(x))

        return z.view(z.size()[0], 1) * h1 + (1 - z).view(z.size()[0], 1) * h2

class MLPDecoder(nn.Module):
    def __init__(self,
                 in_units,
                 dropout_rate=0.2):
        super(MLPDecoder, self).__init__()
        self.dropout = nn.Dropout(dropout_rate)
        self.sigmoid = nn.Sigmoid()

        self.lin1 = nn.Linear(8 * in_units, 128)
        self.lin2 = nn.Linear(128, 64)
        self.lin3 = nn.Linear(64, 1)

        self.reset_parameters()

    def reset_parameters(self):
        self.lin1.reset_parameters()
        self.lin2.reset_parameters()
        self.lin3.reset_parameters()

    def forward(self, graph, drug_feat, dis_feat):
        with graph.local_scope():
            graph.nodes['drug'].data['h'] = drug_feat
            graph.nodes['disease'].data['h'] = dis_feat
            graph.apply_edges(udf_u_mul_e)
            out = graph.edata['m']

            out = F.relu(self.lin1(out))
            out = self.dropout(out)

            out = F.relu(self.lin2(out))
            out = self.dropout(out)

            # out = self.sigmoid(self.lin3(out))
            out = self.lin3(out)
        return out


def udf_u_mul_e_norm(edges):
    return {'reg': edges.src['reg'] * edges.dst['ci']}
    # out_feats = edges.src['reg'].shape[1] // 3 return {'reg' : th.cat([edges.src['reg'][:, :out_feats] * edges.dst[
    # 'ci'], edges.src['reg'][:, out_feats:out_feats*2], edges.src['reg'][:, out_feats*2:]], 1)}


def udf_u_mul_e(edges):
    return {'m': th.cat([edges.src['h'], edges.dst['h']], 1)}
    # return {'m': (edges.src['h']) * (edges.dst['h'])}


def dot_or_identity(A, B, device=None):
    # if A is None, treat as identity matrix. A feat, B weight
    # feat size [313, 3] weight size [909, 600]
    if A is None:
        return B
    elif A.shape[1] == 3:
        if device is None:
            return th.cat([B[A[:, 0].long()], B[A[:, 1].long()], B[A[:, 2].long()]], 1)
        else:
            # return th.cat([B[A[:, 0].long()], B[A[:, 2].long()]], 1).to(device)  # only train one-hop
            # return th.cat([B[A[:, 0].long()], B[A[:, 1].long()]], 1).to(device)  # only train two-hop
            # return B[A[:, 0].long()].to(device)
            return th.cat([B[A[:, 0].long()], B[A[:, 1].long()], B[A[:, 2].long()]], 1).to(device)
    else:
        return A

def apply_logistic(tensor, c, d, ts):
    # 使用tensor的clone来创建一个修改后的副本
    transformed_tensor = tensor.clone()
    # 小于0.8的索引位置
    mask = tensor < ts
    # 应用函数到小于0.8的数上
    transformed_tensor[mask] = logistic_similarity_transform(tensor[mask], c, d)
    return transformed_tensor
def apply_logistic_top(tensor, c, d, ts):
    # 使用tensor的clone来创建一个修改后的副本
    transformed_tensor = tensor.clone()
    # 对tensor中每一行，保留前五个最高的数，其他的数进行logistic_similarity_transform操作
    for i in range(tensor.size(0)):
        # 取出每一行
        row = tensor[i]
        # 通过torch.topk获取每一行中的前五个最大值的索引
        top5_indices = th.topk(row, ts).indices
        # 创建一个mask，默认所有元素都需要transform
        mask = th.ones_like(row, dtype=th.bool)
        # 将前五个最大值的位置设置为不需要transform
        mask[top5_indices] = False
        # 应用logistic_similarity_transform函数到需要转换的位置上
        transformed_tensor[i][mask] = logistic_similarity_transform(row[mask], c, d)
    return transformed_tensor
class Net(nn.Module):
    def __init__(self, args):
        super(Net, self).__init__()
        self.layers = args.layers
        self._act = get_activation(args.model_activation)
        self.TGCN = nn.ModuleList()
        self.TGCN.append(GCMCLayer(args.rating_vals,  # [0, 1]
                                   args.src_in_units,  # 909
                                   args.dst_in_units,  # 909
                                   args.gcn_agg_units,  # 1800
                                   args.gcn_out_units,  # 75
                                   args.dropout,  # 0.3
                                   args.gcn_agg_accum,  # sum
                                   agg_act=self._act,  # Tanh()
                                   share_user_item_param=args.share_param,  # True
                                   device=args.device))
        self.gcn_agg_accum = args.gcn_agg_accum  # sum
        self.rating_vals = args.rating_vals  # sum[0, 1]
        self.device = args.device
        self.gcn_agg_units = args.gcn_agg_units  # 1800
        self.src_in_units = args.src_in_units  # 909
        self.c = args.c
        self.d = args.d

        self.nb = args.num_neighbor
        for i in range(1, args.layers):
            if args.gcn_agg_accum == 'stack':
                gcn_out_units = args.gcn_out_units * len(args.rating_vals)
            else:
                gcn_out_units = args.gcn_out_units
            self.TGCN.append(GCMCLayer(args.rating_vals,  # [0, 1]
                                       args.gcn_out_units,  # 75
                                       args.gcn_out_units,  # 75
                                       gcn_out_units,  # 75
                                       args.gcn_out_units,  # 75
                                       args.dropout,
                                       args.gcn_agg_accum,
                                       agg_act=self._act,
                                       share_user_item_param=args.share_param,
                                       device=args.device))

        self.FGCN = FGCN(args.fdim_drug,
                         args.fdim_disease,
                         args.nhid1,
                         args.nhid2,
                         args.dropout)
        self.FGAT = FGAT(args.fdim_drug,
                         args.fdim_disease,
                         args.nhid1,
                         args.nhid2,
                         args.dropout)

        self.attention = Attention(args.gcn_out_units)
        self.decoder = MLPDecoder(in_units=args.gcn_out_units)
        self.rating_vals = args.rating_vals
        self.gatedfusion = GatedMultimodalLayer(2*args.gcn_out_units, 2*args.gcn_out_units, 2*args.gcn_out_units)

    def forward(self, enc_graph, dec_graph,
                drug_graph1, drug_graph2, drug_sim_feat, drug_feat,
                dis_graph1, dis_graph2, disease_sim_feat, dis_feat,
                Two_Stage=False):

        # Topology convolution operation
        drug_out, dis_out = None, None
        drug_list = []
        dis_list = []
        for i in range(0, self.layers):
            drug_o, dis_o = self.TGCN[i](enc_graph, drug_feat, dis_feat, Two_Stage)
            drug_list.append(drug_o)
            dis_list.append(dis_o)

            drug_feat = drug_o
            dis_feat = dis_o
        drug_out = th.stack(drug_list, dim=1)
        drug_out, att_drug = self.attention(drug_out)
        dis_out = th.stack(dis_list, dim=1)
        dis_out, att_dis = self.attention(dis_out)

        # Feature convolution operation
        #drug_sim_feat = apply_logistic_top(drug_sim_feat, self.c, self.d, self.nb)
        #disease_sim_feat = apply_logistic_top(disease_sim_feat, self.c, self.d, self.nb)
        drug_sim_out1, dis_sim_out1 = self.FGCN(drug_graph1, drug_sim_feat,
                                              dis_graph1, disease_sim_feat)
        drug_sim_out2, dis_sim_out2 = self.FGCN(drug_graph2, drug_sim_feat,
                                                dis_graph2, disease_sim_feat)

        drug_feat1 = th.cat((drug_out, drug_sim_out1), 1)
        drug_feat2 = th.cat((drug_out, drug_sim_out2), 1)
        dis_feat1 = th.cat((dis_out, dis_sim_out1), 1)
        dis_feat2 = th.cat((dis_out, dis_sim_out2), 1)
        # Attention operation
        #drug_sim_out = th.stack([drug_sim_out1, drug_sim_out2], dim=1)
        #drug_sim_out, att_drug = self.attention(drug_sim_out)
        #dis_sim_out = th.stack([dis_sim_out1, dis_sim_out2], dim=1)
        #dis_sim_out, att_dis = self.attention(dis_sim_out)

        #drug_feat = th.cat((drug_out, drug_sim_out), 1)
        #dis_feat = th.cat((dis_out, dis_sim_out), 1)
        #++++++ Gated multimodal fusion +++++++
        #drug_feat = self.gatedfusion(drug_feat1, drug_feat2)
        #dis_feat = self.gatedfusion(dis_feat1, dis_feat2)
        drug_feat = th.cat((drug_feat1, drug_feat2), 1)
        dis_feat = th.cat((dis_feat1, dis_feat2), 1)

        pred_ratings = self.decoder(dec_graph, drug_feat, dis_feat)
        return pred_ratings, drug_list[0], drug_list[1], drug_sim_out1, drug_sim_out2, dis_list[0], dis_list[0], dis_sim_out1, dis_sim_out2
